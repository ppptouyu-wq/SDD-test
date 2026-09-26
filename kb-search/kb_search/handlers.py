"""HTTP 请求处理逻辑（**不依赖任何 Web 框架**）。

为什么要单独抽出这一层：本环境装不上 FastAPI，如果业务逻辑写在框架的装饰器函数里，
整个 HTTP 层就完全无法验证。把"参数校验 + 错误码映射 + 响应体构造"抽成纯函数后：

  - 这部分的全部行为都能被单元测试覆盖（不依赖 fastapi）
  - `api.py` 退化为极薄的适配壳，框架相关代码降到十几行
  - 将来换 Web 框架（或加 gRPC 入口）时，这一层不用改

这是本项目对"接口契约"的一个具体实践：**契约（输入 → 输出/错误码）与承载契约的
技术（FastAPI）解耦**。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from kb_search.models import (
    DEFAULT_TOP_K,
    MAX_TOP_K,
    KbError,
    ValidationError,
)

# HTTP 状态码语义（design.md §6.1）
STATUS_OK = 200
STATUS_BAD_REQUEST = 400
STATUS_SERVICE_UNAVAILABLE = 503


class HandlerResult:
    """一次请求处理的结果：状态码 + 响应体。"""

    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self.body = body

    def __repr__(self) -> str:  # pragma: no cover - 便于调试
        return f"HandlerResult(status={self.status_code}, body={self.body})"


def serialize_hits(hits) -> dict[str, Any]:
    """检索结果 → 响应体。"""
    return {"hits": [asdict(hit) for hit in hits], "count": len(hits)}


def serialize_report(report) -> dict[str, Any]:
    """同步报告 → 响应体（含 failed_doc_ids 便于调用方定位）。"""
    payload = asdict(report)
    payload["failed_doc_ids"] = report.failed_doc_ids
    return payload


def handle_search(search_service, query: str = "", top_k: int | None = None) -> HandlerResult:
    """处理 POST /search。

    状态码约定（design.md §6.1 + proposal.md §3.3）：
      - 正常            -> 200
      - query 为空/超长  -> 400
      - 向量库不可用     -> 503
    """
    try:
        hits = search_service.search(query, top_k)
    except ValidationError as exc:
        return HandlerResult(STATUS_BAD_REQUEST, {"detail": exc.message})
    except KbError as exc:
        return HandlerResult(STATUS_SERVICE_UNAVAILABLE, {"detail": exc.message})
    return HandlerResult(STATUS_OK, serialize_hits(hits))


def handle_ingest(ingest_service, doc_ids: list[str] | None = None) -> HandlerResult:
    """处理 POST /ingest。外部依赖不可用返回 503。"""
    try:
        report = ingest_service.sync(doc_ids)
    except KbError as exc:
        return HandlerResult(STATUS_SERVICE_UNAVAILABLE, {"detail": exc.message})
    return HandlerResult(STATUS_OK, serialize_report(report))


def handle_healthz(store=None) -> HandlerResult:
    """处理 GET /healthz。"""
    body: dict[str, Any] = {"status": "ok"}
    if store is not None:
        try:
            body["chunks"] = store.count()
        except KbError as exc:
            return HandlerResult(
                STATUS_SERVICE_UNAVAILABLE,
                {"status": "degraded", "detail": exc.message},
            )
    else:
        body["chunks"] = None
    return HandlerResult(STATUS_OK, body)


def normalize_search_request(payload: dict[str, Any] | None) -> tuple[str, int | None]:
    """从原始请求体提取 (query, top_k)，对类型做容错。

    越界截断由 service 层负责（proposal.md §3.3：top_k 超上限按上限截断，不报错）。
    """
    payload = payload or {}
    query = payload.get("query") or ""
    raw_top_k = payload.get("top_k")
    if raw_top_k is None:
        return str(query), None
    try:
        return str(query), int(raw_top_k)
    except (TypeError, ValueError):
        return str(query), DEFAULT_TOP_K


__all__ = [
    "STATUS_BAD_REQUEST",
    "STATUS_OK",
    "STATUS_SERVICE_UNAVAILABLE",
    "HandlerResult",
    "MAX_TOP_K",
    "handle_healthz",
    "handle_ingest",
    "handle_search",
    "normalize_search_request",
    "serialize_hits",
    "serialize_report",
]
