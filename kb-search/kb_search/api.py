"""HTTP 接入层（FastAPI 薄壳）。

设计：**业务逻辑全部在 `handlers.py`**（不依赖 Web 框架、可完整单元测试），
本文件只负责把 HTTP 请求翻译成 handler 调用，再把 `HandlerResult` 翻译成响应。
整个文件里与框架相关的代码不到 30 行。

用法：
    uvicorn kb_search.api:create_demo_app --factory --reload

依赖说明：`fastapi` 在 pyproject.toml 的 `[service]` 可选依赖组里 ——
只需用领域逻辑时不必安装它；`handlers.py` 与 `service.py` 不依赖任何 Web 框架。
"""

from __future__ import annotations

from typing import Any

from kb_search.handlers import (
    HandlerResult,
    handle_healthz,
    handle_ingest,
    handle_search,
    normalize_search_request,
)

__all__ = [
    "build_report_payload",
    "create_app",
    "create_demo_app",
    "register_routes",
]


def build_report_payload(report) -> dict[str, Any]:
    """兼容旧接口名，转调 handlers.serialize_report。"""
    from kb_search.handlers import serialize_report

    return serialize_report(report)


def register_routes(app, search_service, ingest_service, *, store=None) -> None:
    """把三个路由注册到给定的 FastAPI 应用上（仅做适配，无业务逻辑）。"""
    from fastapi import Body
    from fastapi.responses import JSONResponse

    def _respond(result: HandlerResult) -> JSONResponse:
        return JSONResponse(status_code=result.status_code, content=result.body)

    @app.post("/search")
    def search(payload: dict = Body(default={})):  # noqa: B008 - FastAPI 惯用法
        query, top_k = normalize_search_request(payload)
        return _respond(handle_search(search_service, query, top_k))

    @app.post("/ingest")
    def ingest(payload: dict = Body(default={})):  # noqa: B008
        doc_ids = (payload or {}).get("doc_ids")
        return _respond(handle_ingest(ingest_service, doc_ids))

    @app.get("/healthz")
    def healthz():
        return _respond(handle_healthz(store))


def create_app(*, search_service, ingest_service, store=None):
    """用注入的服务构造应用（便于测试与替换实现）。"""
    from fastapi import FastAPI

    app = FastAPI(title="知识库语义搜索工具", version="1.0.0")
    register_routes(app, search_service, ingest_service, store=store)
    return app


def create_demo_app():
    """本地演示：Mock 飞书 + 本地向量化 + 内存向量库，无需任何外部凭据。"""
    from kb_search.demo_data import build_demo_stack

    search_service, ingest_service, store = build_demo_stack()
    ingest_service.sync()  # 启动时先灌一次演示数据
    return create_app(
        search_service=search_service, ingest_service=ingest_service, store=store
    )
