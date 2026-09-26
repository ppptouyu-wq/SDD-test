"""向量库：接口 + 内存实现 + Qdrant 实现。

契约来源：specs/design.md §4、specs/tasks.md Task 3
    - InMemoryVectorStore 支持 upsert / search / count / get_content_hash
    - search() 结果按余弦相似度降序，且携带来源 doc_id 与标题
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Protocol, Sequence

from kb_search.models import Chunk, SearchHit, VectorStoreError


class VectorStore(Protocol):
    """向量库接口。"""

    def upsert(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> int:
        ...

    def search(self, query_vector: Sequence[float], top_k: int) -> list[SearchHit]:
        ...

    def count(self) -> int:
        ...

    def get_content_hash(self, doc_id: str) -> str | None:
        ...


def content_hash(text: str) -> str:
    """文档内容哈希，用于增量同步判断是否变更（design.md §6.3）。"""
    return hashlib.blake2b((text or "").encode("utf-8"), digest_size=16).hexdigest()


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度，范围 0~1（向量均为非负，故不会为负）。"""
    if len(a) != len(b):
        raise VectorStoreError(f"向量维度不一致：{len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class InMemoryVectorStore:
    """内存向量库：本地运行、CI 与测试用。

    检索是精确暴力计算（本规模下足够），不引入近似索引以保持结果确定可复现。
    """

    def __init__(self) -> None:
        self._chunks: dict[str, Chunk] = {}
        self._vectors: dict[str, tuple[float, ...]] = {}
        self._doc_hashes: dict[str, str] = {}

    # ---- 写入 ----
    def upsert(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> int:
        if len(chunks) != len(vectors):
            raise VectorStoreError(
                f"片段数与向量数不匹配：{len(chunks)} vs {len(vectors)}"
            )
        # 同一文档重新写入前先清掉旧片段，避免重复召回
        doc_ids = {c.doc_id for c in chunks}
        for doc_id in doc_ids:
            self.delete_by_doc(doc_id)

        for chunk, vector in zip(chunks, vectors):
            self._chunks[chunk.chunk_id] = chunk
            self._vectors[chunk.chunk_id] = tuple(float(v) for v in vector)
        return len(chunks)

    def set_content_hash(self, doc_id: str, text: str) -> None:
        self._doc_hashes[doc_id] = content_hash(text)

    def delete_by_doc(self, doc_id: str) -> int:
        removed = [cid for cid, c in self._chunks.items() if c.doc_id == doc_id]
        for cid in removed:
            self._chunks.pop(cid, None)
            self._vectors.pop(cid, None)
        return len(removed)

    # ---- 查询 ----
    def search(self, query_vector: Sequence[float], top_k: int) -> list[SearchHit]:
        scored: list[tuple[float, Chunk]] = []
        for chunk_id, chunk in self._chunks.items():
            score = cosine_similarity(query_vector, self._vectors[chunk_id])
            if score > 0:
                scored.append((score, chunk))

        # 分数降序；同分按文档与位置稳定排序，保证结果可复现
        scored.sort(key=lambda pair: (-pair[0], pair[1].doc_id, pair[1].position))

        return [
            SearchHit(
                chunk_id=chunk.chunk_id,
                doc_id=chunk.doc_id,
                title=chunk.title,
                url=chunk.url,
                text=chunk.text,
                score=round(score, 6),
            )
            for score, chunk in scored[: max(top_k, 0)]
        ]

    def count(self) -> int:
        return len(self._chunks)

    def get_content_hash(self, doc_id: str) -> str | None:
        return self._doc_hashes.get(doc_id)

    def doc_ids(self) -> list[str]:
        return sorted({c.doc_id for c in self._chunks.values()})


class QdrantVectorStore:
    """Qdrant 向量库（ADR-001）。

    真实实现：需要 Qdrant 服务可达。未安装 qdrant-client 或服务不可达时，
    构造或调用会抛出 VectorStoreError（带原因），不做静默降级 ——
    静默降级会让上层误以为数据已入库（与 design.md §6.1 关键原则 2 一致）。
    """

    def __init__(
        self,
        *,
        url: str = "http://localhost:6333",
        collection: str = "kb_chunks",
        api_key: str | None = None,
        dimension: int = 1536,
    ) -> None:
        self.url = url
        self.collection = collection
        self.api_key = api_key
        self.dimension = dimension
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from qdrant_client import QdrantClient
        except ImportError as exc:
            raise VectorStoreError(
                "缺少依赖 qdrant-client，无法连接 Qdrant（pip install qdrant-client）"
            ) from exc
        try:
            self._client = QdrantClient(url=self.url, api_key=self.api_key)
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"连接 Qdrant 失败（{self.url}）：{exc}") from exc
        return self._client

    def _ensure_collection(self) -> None:
        client = self._get_client()
        existing = [c.name for c in client.get_collections().collections]
        if self.collection in existing:
            return
        try:
            from qdrant_client.models import Distance, VectorParams

            client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(size=self.dimension, distance=Distance.COSINE),
            )
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"创建 Qdrant 集合失败：{exc}") from exc

    def upsert(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> int:
        if len(chunks) != len(vectors):
            raise VectorStoreError(
                f"片段数与向量数不匹配：{len(chunks)} vs {len(vectors)}"
            )
        self._ensure_collection()
        client = self._get_client()
        try:
            from qdrant_client.models import PointStruct

            points = [
                PointStruct(
                    id=abs(hash(chunk.chunk_id)) % (10**12),
                    vector=list(vector),
                    payload={
                        "chunk_id": chunk.chunk_id,
                        "doc_id": chunk.doc_id,
                        "title": chunk.title,
                        "url": chunk.url,
                        "text": chunk.text,
                        "position": chunk.position,
                    },
                )
                for chunk, vector in zip(chunks, vectors)
            ]
            client.upsert(collection_name=self.collection, points=points)
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"写入 Qdrant 失败：{exc}") from exc
        return len(chunks)

    def search(self, query_vector: Sequence[float], top_k: int) -> list[SearchHit]:
        client = self._get_client()
        try:
            response = client.query_points(
                collection_name=self.collection,
                query=list(query_vector),
                limit=max(top_k, 1),
                with_payload=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"检索 Qdrant 失败：{exc}") from exc

        hits: list[SearchHit] = []
        for point in getattr(response, "points", response):
            payload = point.payload or {}
            hits.append(
                SearchHit(
                    chunk_id=payload.get("chunk_id", ""),
                    doc_id=payload.get("doc_id", ""),
                    title=payload.get("title", ""),
                    url=payload.get("url", ""),
                    text=payload.get("text", ""),
                    score=round(float(point.score), 6),
                )
            )
        return hits

    def count(self) -> int:
        client = self._get_client()
        try:
            return int(client.count(collection_name=self.collection).count)
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"统计 Qdrant 片段数失败：{exc}") from exc

    def get_content_hash(self, doc_id: str) -> str | None:
        # 增量判断由 service 层用本地上次同步状态维护；Qdrant 侧不重复存储
        return None

    def health(self) -> bool:
        try:
            self._get_client().get_collections()
            return True
        except VectorStoreError:
            return False
