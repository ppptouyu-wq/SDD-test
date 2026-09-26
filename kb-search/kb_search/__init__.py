"""知识库语义搜索工具。

复现自《SDD 实战：规范驱动开发之道》第 1-2 章的案例项目。
规范见 specs/proposal.md、specs/design.md、specs/tasks.md。
"""

from kb_search.chunking import chunk_document, chunk_text
from kb_search.embeddings import LocalHashEmbedding, OpenAiEmbedding
from kb_search.models import (
    EMBEDDING_DIMENSION,
    Chunk,
    Document,
    IngestReport,
    SearchHit,
    ValidationError,
)
from kb_search.service import IngestService, SearchService
from kb_search.sources import HttpFeishuDocSource, MockFeishuDocSource
from kb_search.vectorstore import InMemoryVectorStore, QdrantVectorStore

__all__ = [
    "EMBEDDING_DIMENSION",
    "Chunk",
    "Document",
    "HttpFeishuDocSource",
    "InMemoryVectorStore",
    "IngestReport",
    "IngestService",
    "LocalHashEmbedding",
    "MockFeishuDocSource",
    "OpenAiEmbedding",
    "QdrantVectorStore",
    "SearchHit",
    "SearchService",
    "ValidationError",
    "chunk_document",
    "chunk_text",
]
