"""FastAPI 适配层测试（可选依赖）。

**业务逻辑已在 `test_handlers.py` 中完整覆盖**（不依赖 Web 框架）。
本文件只验证框架适配这一薄层：路由是否注册、`HandlerResult` 是否正确翻译成
HTTP 状态码与 JSON 响应。

未安装 fastapi 时整体跳过 —— 这不影响接口行为的验证完整性，
因为被跳过的只是框架粘合代码。
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip(
    "fastapi", reason="未安装可选的 [service] 依赖，跳过框架适配层测试"
)
from fastapi.testclient import TestClient  # noqa: E402

from kb_search.api import create_app  # noqa: E402
from kb_search.demo_data import build_demo_stack  # noqa: E402


@pytest.fixture
def client():
    search, ingest, store = build_demo_stack()
    ingest.sync()
    return TestClient(create_app(search_service=search, ingest_service=ingest, store=store))


def test_routes_are_registered(client):
    paths = {route.path for route in client.app.routes}
    assert {"/search", "/ingest", "/healthz"} <= paths


def test_search_endpoint_translates_handler_result(client):
    response = client.post("/search", json={"query": "接口限流", "top_k": 2})

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] <= 2
    assert payload["hits"][0]["doc_id"] == "doc-rate-limit"


def test_blank_query_maps_to_400(client):
    response = client.post("/search", json={"query": "   "})

    assert response.status_code == 400
    assert "query" in response.json()["detail"]


def test_ingest_endpoint_maps_to_200(client):
    response = client.post("/ingest", json={})

    assert response.status_code == 200
    assert response.json()["total"] == 3


def test_healthz_endpoint(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["chunks"] > 0


def test_empty_body_is_accepted(client):
    """缺省请求体不应导致 422 —— 路由层给了默认值。"""
    response = client.post("/search", json={})

    assert response.status_code == 400  # 空 query 的语义错误，而非框架校验错误
    assert "422" not in str(response.status_code)
