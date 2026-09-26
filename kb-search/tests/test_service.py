"""specs/tasks.md Task 4 验收标准：搜索与同步服务。

- query 为空或全空白时返回 400（ValidationError）
- top_k > 100 时按 100 截断，< 1 时按 1 处理
- 向量库为空时返回空结果列表，不抛异常
- 检索到同一文档的多个片段时可追溯到同一文档链接
"""

from __future__ import annotations

import pytest

from kb_search.demo_data import build_demo_stack
from kb_search.embeddings import LocalHashEmbedding
from kb_search.models import (
    DEFAULT_TOP_K,
    MAX_TOP_K,
    EmbeddingError,
    ValidationError,
)
from kb_search.service import (
    IngestService,
    SearchService,
    normalize_query,
    normalize_top_k,
)
from kb_search.sources import MockFeishuDocSource
from kb_search.vectorstore import InMemoryVectorStore


# ----------------------------------------------------------- 入参校验

@pytest.mark.parametrize("bad", ["", "   ", "\n\t ", None])
def test_normalize_query_rejects_blank(bad):
    with pytest.raises(ValidationError) as excinfo:
        normalize_query(bad)  # type: ignore[arg-type]
    assert "query" in str(excinfo.value)


def test_normalize_query_rejects_too_long():
    with pytest.raises(ValidationError):
        normalize_query("字" * 2001)


def test_normalize_query_strips():
    assert normalize_query("  限流  ") == "限流"


@pytest.mark.parametrize(
    "given, expected",
    [
        (None, DEFAULT_TOP_K),
        (3, 3),
        (0, 1),        # < 1 按 1 处理
        (-5, 1),
        (101, MAX_TOP_K),  # > 100 按 100 截断
        (100, 100),
        ("7", 7),      # 字符串数字容错
        ("abc", DEFAULT_TOP_K),
    ],
)
def test_normalize_top_k(given, expected):
    assert normalize_top_k(given) == expected


def test_search_rejects_blank_query(synced_stack):
    search, _, _ = synced_stack
    with pytest.raises(ValidationError):
        search.search("   ")


def test_search_on_empty_store_returns_empty_list():
    """proposal.md §3.3：向量库为空时返回空结果列表。"""
    store = InMemoryVectorStore()
    service = SearchService(LocalHashEmbedding(), store)

    assert service.search("任意查询") == []


def test_search_respects_top_k(synced_stack):
    search, _, store = synced_stack
    assert len(search.search("限流", top_k=1)) <= 1
    assert len(search.search("限流", top_k=100)) <= store.count()


# ------------------------------------------------------------- 同步

def test_ingest_reports_progress():
    search, ingest, store = build_demo_stack()
    report = ingest.sync()

    assert report.total == 3
    assert report.succeeded == 3
    assert report.failed == 0
    assert report.chunks_written == store.count() > 0
    assert len(report.items) == 3
    assert all(item.ok for item in report.items)


def test_ingest_single_doc_failure_does_not_block_others():
    """design.md §6.1：单篇失败不阻断其余文档，且必须标注失败的 doc_id。"""
    from kb_search.demo_data import demo_documents

    source = MockFeishuDocSource(demo_documents(), fail_for=["doc-deploy"])
    embedding = LocalHashEmbedding()
    store = InMemoryVectorStore()
    ingest = IngestService(source, embedding, store)

    report = ingest.sync()

    assert report.total == 3
    assert report.succeeded == 2
    assert report.failed == 1
    assert report.failed_doc_ids == ["doc-deploy"]
    # 其余文档仍成功入库
    assert store.count() > 0
    assert "doc-deploy" not in store.doc_ids()


def test_ingest_skips_unchanged_documents():
    """design.md §6.3：以内容哈希做增量同步。"""
    search, ingest, store = build_demo_stack()
    first = ingest.sync()
    second = ingest.sync()

    assert first.skipped == 0
    assert second.skipped == 3
    assert second.chunks_written == 0
    assert second.succeeded == 0
    assert store.count() == first.chunks_written


def test_ingest_rewrites_when_content_changes():
    from kb_search.demo_data import demo_documents
    from kb_search.models import Document

    docs = demo_documents()
    source = MockFeishuDocSource(docs)
    embedding = LocalHashEmbedding()
    store = InMemoryVectorStore()
    ingest = IngestService(source, embedding, store)
    ingest.sync()

    changed = Document(
        doc_id=docs[0].doc_id, title=docs[0].title, url=docs[0].url,
        content="完全换掉的内容，讲的是另一个主题：成本优化。", updated_at=docs[0].updated_at,
    )
    source._documents[changed.doc_id] = changed
    report = ingest.sync([changed.doc_id])

    assert report.skipped == 0
    assert report.chunks_written > 0
    assert "成本优化" in store.search(embedding.encode_one("成本优化"), 1)[0].text


def test_ingest_empty_content_writes_no_chunk():
    """proposal.md §3.3：正文为空时不写入空片段。"""
    from kb_search.models import Document
    from datetime import datetime, timezone

    empty = Document(
        doc_id="empty", title="空文档", url="https://x/empty",
        content="   \n  ", updated_at=datetime.now(tz=timezone.utc),
    )
    source = MockFeishuDocSource([empty])
    store = InMemoryVectorStore()
    ingest = IngestService(source, LocalHashEmbedding(), store)

    report = ingest.sync()

    assert report.failed == 0          # 空正文不算失败
    assert store.count() == 0
    assert report.chunks_written == 0


def test_ingest_embedding_failure_is_reported_not_raised():
    """Embedding 失败应记为该文档失败，而不是让整个同步崩掉。"""
    from kb_search.demo_data import demo_documents

    class BrokenEmbedding(LocalHashEmbedding):
        def encode(self, texts):
            raise EmbeddingError("Embedding 服务超时")

    source = MockFeishuDocSource(demo_documents())
    store = InMemoryVectorStore()
    ingest = IngestService(source, BrokenEmbedding(), store)

    report = ingest.sync()

    assert report.failed == 3
    assert all("超时" in (item.error or "") for item in report.items)


def test_ingest_subset_of_docs():
    search, ingest, store = build_demo_stack()
    report = ingest.sync(["doc-deploy"])

    assert report.total == 1
    assert store.doc_ids() == ["doc-deploy"]
