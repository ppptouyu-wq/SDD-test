"""向量化：接口 + 本地确定性实现。

契约来源：specs/design.md §4、specs/tasks.md Task 3
    - LocalHashEmbedding 输出维度恒为 1536 且向量已 L2 归一化
    - 相同文本编码结果完全一致（确定性）

为什么提供本地实现（design.md §7）：真实 Embedding 需要外部凭据，
若领域逻辑只能靠外部服务驱动，则无法被测试。本地实现用**词法哈希**产生向量，
使余弦相似度真实反映字面重叠，因此"检索语义相关片段"这类行为可以被断言。
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol, Sequence

from kb_search.models import EMBEDDING_DIMENSION, EmbeddingError

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")


class EmbeddingModel(Protocol):
    """向量化模型接口。"""

    dimension: int

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        ...


# 特征权重：中文二元组与英文词为主特征，中文单字降权以压制字面巧合
_BIGRAM_WEIGHT = 1.0
_UNIGRAM_WEIGHT = 0.15


class LocalHashEmbedding:
    """本地确定性向量化：n-gram 哈希 + L2 归一化。

    不依赖任何外部服务，用于本地运行、CI 与测试。
    """

    def __init__(self, dimension: int = EMBEDDING_DIMENSION) -> None:
        if dimension <= 0:
            raise ValueError("dimension 必须为正数")
        self.dimension = dimension

    def _bucket(self, token: str) -> int:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") % self.dimension

    def _weights(self, text: str) -> list[tuple[str, float]]:
        """给每个特征分配权重：中文二元组/英文词为 1.0，中文单字为 0.15。"""
        weighted: list[tuple[str, float]] = []
        for match in _TOKEN_RE.finditer(text or ""):
            piece = match.group(0)
            if piece.isascii():
                weighted.append((piece.lower(), _BIGRAM_WEIGHT))
                continue
            chars = list(piece)
            if len(chars) == 1:
                weighted.append((chars[0], _UNIGRAM_WEIGHT))
                continue
            for i in range(len(chars) - 1):
                weighted.append(("".join(chars[i : i + 2]), _BIGRAM_WEIGHT))
            for char in chars:
                weighted.append((char, _UNIGRAM_WEIGHT))
        return weighted

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        if isinstance(texts, str):
            raise TypeError("encode() 需要文本序列，不是单个字符串")
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimension
            weighted = self._weights(text)
            if not weighted:
                vectors.append(vector)  # 全零向量：无特征
                continue
            for token, weight in weighted:
                vector[self._bucket(token)] += weight
            norm = math.sqrt(sum(v * v for v in vector))
            if norm > 0:
                vector = [v / norm for v in vector]
            vectors.append(vector)
        return vectors

    def encode_one(self, text: str) -> list[float]:
        return self.encode([text])[0]


class OpenAiEmbedding:
    """真实 Embedding（text-embedding-3-small，ADR-002）。

    需要 `OPENAI_API_KEY`。此处实现为惰性构造，未配置凭据时在调用时报错，
    而不是在导入时崩溃 —— 这样本地与 CI 不必配置凭据也能跑测试。
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "text-embedding-3-small",
        dimension: int = EMBEDDING_DIMENSION,
        timeout: float = 20.0,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self.dimension = dimension
        self.timeout = timeout

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        if not self._api_key:
            raise EmbeddingError("缺少环境变量 OPENAI_API_KEY，无法调用 Embedding 服务")
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise EmbeddingError("需要 httpx 才能调用 Embedding 服务") from exc

        try:
            response = httpx.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": self.model, "input": list(texts)},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - 统一转领域异常
            raise EmbeddingError(f"Embedding 服务调用失败：{exc}") from exc

        payload = response.json()
        data = sorted(payload.get("data", []), key=lambda d: d.get("index", 0))
        vectors = [d["embedding"] for d in data]
        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"Embedding 返回条数不匹配：期望 {len(texts)}，实际 {len(vectors)}"
            )
        return vectors
