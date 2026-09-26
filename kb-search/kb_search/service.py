"""领域服务层：检索与同步两条流程的编排。

契约来源：specs/design.md §4、§6.1；specs/tasks.md Task 4
    - search: query 为空返回 400（ValidationError）；top_k 越界截断；
      向量库为空返回空列表不抛异常
    - sync: 单篇失败不阻断其余文档；内容未变更则跳过
"""

from __future__ import annotations

from typing import Sequence

from kb_search.chunking import chunk_document
from kb_search.models import (
    DEFAULT_TOP_K,
    MAX_QUERY_LENGTH,
    MAX_TOP_K,
    Document,
    EmbeddingError,
    IngestItemResult,
    IngestReport,
    SearchHit,
    SourceError,
    ValidationError,
)
from kb_search.vectorstore import VectorStore, content_hash


def normalize_query(query: str) -> str:
    """校验并规范化查询词（proposal.md §3.3）。"""
    text = (query or "").strip()
    if not text:
        raise ValidationError("query 不能为空或全为空白字符")
    if len(text) > MAX_QUERY_LENGTH:
        raise ValidationError(f"query 长度不得超过 {MAX_QUERY_LENGTH} 个字符")
    return text


def normalize_top_k(top_k: int | None) -> int:
    """top_k 越界时按边界截断，而不是报错（proposal.md §3.3）。"""
    if top_k is None:
        return DEFAULT_TOP_K
    try:
        value = int(top_k)
    except (TypeError, ValueError):
        return DEFAULT_TOP_K
    if value < 1:
        return 1
    return min(value, MAX_TOP_K)


class SearchService:
    """检索服务。"""

    def __init__(self, embedding, store: VectorStore) -> None:
        self._embedding = embedding
        self._store = store

    def search(self, query: str, top_k: int | None = None) -> list[SearchHit]:
        text = normalize_query(query)
        limit = normalize_top_k(top_k)

        # 向量库为空时直接返回空结果，不消耗 Embedding 调用（design.md §6.1）
        if self._store.count() == 0:
            return []

        vectors = self._embedding.encode([text])
        if not vectors:
            raise EmbeddingError("向量化返回空结果")
        return self._store.search(vectors[0], limit)


class IngestService:
    """同步服务：拉取文档 → 切分 → 向量化 → 写入。"""

    def __init__(self, source, embedding, store: VectorStore, *, chunk_kwargs=None) -> None:
        self._source = source
        self._embedding = embedding
        self._store = store
        self._chunk_kwargs = chunk_kwargs or {}

    def sync(self, doc_ids: Sequence[str] | None = None) -> IngestReport:
        target_ids = list(doc_ids) if doc_ids else list(self._source.list_documents())
        report = IngestReport(total=len(target_ids))

        for doc_id in target_ids:
            try:
                document = self._source.fetch(doc_id)
            except SourceError as exc:
                # 单篇失败不阻断其余文档（design.md §6.1）
                report.items.append(
                    IngestItemResult(doc_id=doc_id, ok=False, error=exc.message)
                )
                report.failed += 1
                continue

            item = self._ingest_one(document)
            report.items.append(item)
            if item.ok:
                if item.skipped:
                    report.skipped += 1
                else:
                    report.succeeded += 1
                    report.chunks_written += item.chunks
            else:
                report.failed += 1

        return report

    def _ingest_one(self, document: Document) -> IngestItemResult:
        # 增量：内容未变更则跳过（design.md §6.3）
        previous = self._store.get_content_hash(document.doc_id)
        if previous is not None and previous == content_hash(document.content):
            return IngestItemResult(
                doc_id=document.doc_id, ok=True, chunks=0, skipped=True
            )

        chunks = chunk_document(document, **self._chunk_kwargs)
        if not chunks:
            # 正文为空：不写空片段，但视为处理成功
            self._store.set_content_hash(document.doc_id, document.content)
            return IngestItemResult(doc_id=document.doc_id, ok=True, chunks=0)

        try:
            vectors = self._embedding.encode([c.text for c in chunks])
        except EmbeddingError as exc:
            return IngestItemResult(
                doc_id=document.doc_id, ok=False, error=exc.message
            )

        written = self._store.upsert(chunks, vectors)
        self._store.set_content_hash(document.doc_id, document.content)
        return IngestItemResult(doc_id=document.doc_id, ok=True, chunks=written)
