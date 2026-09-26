"""数据模型与领域异常。

契约来源：specs/design.md §3、specs/contracts/data-models.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

EMBEDDING_DIMENSION = 1536  # ADR-002：text-embedding-3-small
DEFAULT_TOP_K = 5
MAX_TOP_K = 100
MAX_QUERY_LENGTH = 2000


@dataclass
class Document:
    """飞书文档"""

    doc_id: str
    title: str
    url: str
    content: str
    updated_at: datetime


@dataclass
class Chunk:
    """文档切分后的最小检索单元"""

    chunk_id: str
    doc_id: str
    title: str
    url: str
    text: str
    position: int


@dataclass
class SearchHit:
    """一条检索结果"""

    chunk_id: str
    doc_id: str
    title: str
    url: str
    text: str
    score: float


@dataclass
class IngestItemResult:
    """单篇文档的同步结果"""

    doc_id: str
    ok: bool
    chunks: int = 0
    skipped: bool = False
    error: str | None = None


@dataclass
class IngestReport:
    """一次同步的汇总"""

    total: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    chunks_written: int = 0
    items: list[IngestItemResult] = field(default_factory=list)

    @property
    def failed_doc_ids(self) -> list[str]:
        return [i.doc_id for i in self.items if not i.ok]


# ----------------------------------------------------------------- 异常

class KbError(Exception):
    """本服务所有异常的基类。"""

    def __init__(self, message: str, *, doc_id: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.doc_id = doc_id

    def __str__(self) -> str:
        if self.doc_id:
            return f"[{self.doc_id}] {self.message}"
        return self.message


class SourceError(KbError):
    """飞书文档读取失败。消息必须含 doc_id（design.md §6.1）。"""


class EmbeddingError(KbError):
    """向量化失败。"""


class VectorStoreError(KbError):
    """向量库不可用。"""


class ValidationError(KbError):
    """入参校验失败（映射为 HTTP 400）。"""
