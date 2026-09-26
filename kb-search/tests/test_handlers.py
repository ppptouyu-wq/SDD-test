"""HTTP 处理逻辑测试（不依赖 FastAPI）。

覆盖 specs/tasks.md Task 4 的全部验收标准：
- 返回不超过 top_k 条结果
- 结果按相似度降序，含相似度分数
- query 为空或全空白时返回 400
- top_k > 100 时按 100 截断，< 1 时按 1 处理
- 向量库为空时返回空结果列表，不抛异常
- 同一文档的多个片段可追溯到同一链接

这一层是本项目 HTTP 接口的**全部业务逻辑**；`api.py` 只是把它接到 FastAPI 上。
因此即使环境装不上 FastAPI，接口行为依然是可验证的。
"""

from __future__ import annotations

import pytest

from kb_search.demo_data import build_demo_stack
from kb_search.handlers import (
    STATUS_BAD_REQUEST,
    STATUS_OK,
    STATUS_SERVICE_UNAVAILABLE,
    handle_healthz,
    handle_ingest,
    handle_search,
    normalize_search_request,
    serialize_hits,
    serialize_report,
)


@pytest.fixture
def stack():
    search, ingest, store = build_demo_stack()
    ingest.sync()
    return search, ingest, store


# ==================================================== GET /healthz

def test_healthz_reports_chunk_count(stack):
    _search, _ingest, store = stack
    result = handle_healthz(store)

    assert result.status_code == STATUS_OK
    assert result.body["status"] == "ok"
    assert result.body["chunks"] > 0


def test_healthz_without_store_has_null_count():
    result = handle_healthz(None)
    assert result.status_code == STATUS_OK
    assert result.body["chunks"] is None


def test_healthz_degraded_when_store_unavailable():
    class BrokenStore:
        def count(self):
            from kb_search.models import VectorStoreError

            raise VectorStoreError("Qdrant 连接失败")

    result = handle_healthz(BrokenStore())

    assert result.status_code == STATUS_SERVICE_UNAVAILABLE
    assert result.body["status"] == "degraded"


# ==================================================== POST /search

def test_search_returns_hits_sorted_desc(stack):
    search, _ingest, _store = stack
    result = handle_search(search, "接口限流", 3)

    assert result.status_code == STATUS_OK
    assert result.body["count"] <= 3
    assert result.body["hits"], "应至少命中一条"
    scores = [h["score"] for h in result.body["hits"]]
    assert scores == sorted(scores, reverse=True)
    assert set(result.body["hits"][0]) == {
        "chunk_id", "doc_id", "title", "url", "text", "score"
    }


def test_search_semantic_relevance(stack):
    search, _ingest, _store = stack
    result = handle_search(search, "接口限流怎么做", 1)

    assert result.body["hits"][0]["doc_id"] == "doc-rate-limit"


@pytest.mark.parametrize("bad_query", ["", "   ", "\n\t "])
def test_search_blank_query_returns_400(stack, bad_query):
    """proposal.md §3.3：query 为空或全空白时返回 400。"""
    search, _ingest, _store = stack
    result = handle_search(search, bad_query, 3)

    assert result.status_code == STATUS_BAD_REQUEST
    assert "query" in result.body["detail"]


def test_search_over_length_query_returns_400(stack):
    search, _ingest, _store = stack
    result = handle_search(search, "字" * 2001, 3)

    assert result.status_code == STATUS_BAD_REQUEST


@pytest.mark.parametrize("top_k", [999, 101, 1000])
def test_search_top_k_over_limit_is_truncated_not_rejected(stack, top_k):
    """proposal.md §3.3：top_k 超过上限按上限截断，而不是报错。"""
    search, _ingest, _store = stack
    result = handle_search(search, "限流", top_k)

    assert result.status_code == STATUS_OK
    assert result.body["count"] <= 100


@pytest.mark.parametrize("top_k", [0, -5])
def test_search_top_k_below_one_is_clamped(stack, top_k):
    search, _ingest, _store = stack
    result = handle_search(search, "限流", top_k)

    assert result.status_code == STATUS_OK
    assert result.body["count"] <= 1


def test_search_default_top_k(stack):
    search, _ingest, _store = stack
    result = handle_search(search, "限流")

    assert result.status_code == STATUS_OK
    assert result.body["count"] <= 5


def test_search_on_empty_store_returns_empty_list():
    """proposal.md §3.3：向量库为空时返回空结果列表，而不是抛异常。"""
    search, ingest, store = build_demo_stack()  # 故意不 sync
    result = handle_search(search, "任意查询", 5)

    assert result.status_code == STATUS_OK
    assert result.body == {"hits": [], "count": 0}


def test_search_vector_store_failure_returns_503(stack):
    """design.md §6.1：向量库不可用 -> 503。"""
    from kb_search.models import VectorStoreError
    from kb_search.service import SearchService

    class BrokenStore:
        def count(self):
            return 1

        def search(self, query_vector, top_k):
            raise VectorStoreError("Qdrant 连接失败")

    search = SearchService(build_demo_stack()[0]._embedding, BrokenStore())
    result = handle_search(search, "限流", 3)

    assert result.status_code == STATUS_SERVICE_UNAVAILABLE
    assert "Qdrant" in result.body["detail"]


def test_search_multiple_chunks_share_document_link(stack):
    """proposal.md §3.1：同一文档的多个片段应可追溯到同一链接。"""
    search, _ingest, _store = stack
    result = handle_search(search, "限流", 10)

    by_doc: dict[str, set[str]] = {}
    for hit in result.body["hits"]:
        by_doc.setdefault(hit["doc_id"], set()).add(hit["url"])
    assert all(len(urls) == 1 for urls in by_doc.values())


# ==================================================== POST /ingest

def test_ingest_returns_report(stack):
    _search, ingest, _store = stack
    result = handle_ingest(ingest)

    assert result.status_code == STATUS_OK
    assert result.body["total"] == 3
    assert "failed_doc_ids" in result.body
    assert result.body["skipped"] == 3  # 夹具已同步过一次


def test_ingest_subset(stack):
    _search, ingest, _store = stack
    result = handle_ingest(ingest, ["doc-deploy"])

    assert result.body["total"] == 1


def test_ingest_dependency_failure_returns_503():
    from kb_search.models import SourceError

    class BrokenIngest:
        def sync(self, doc_ids=None):
            raise SourceError("飞书凭据缺失")

    result = handle_ingest(BrokenIngest())

    assert result.status_code == STATUS_SERVICE_UNAVAILABLE
    assert "凭据" in result.body["detail"]


# ============================================ 请求体解析

@pytest.mark.parametrize(
    "payload, expected_query, expected_top_k",
    [
        (None, "", None),
        ({}, "", None),
        ({"query": "限流"}, "限流", None),
        ({"query": "限流", "top_k": 7}, "限流", 7),
        ({"query": "限流", "top_k": "7"}, "限流", 7),
        ({"query": "限流", "top_k": "abc"}, "限流", 5),  # 非法值回落默认
        ({"query": None, "top_k": None}, "", None),
    ],
)
def test_normalize_search_request(payload, expected_query, expected_top_k):
    assert normalize_search_request(payload) == (expected_query, expected_top_k)


def test_serializers_produce_json_ready_dicts(stack):
    search, ingest, _store = stack

    hits_payload = serialize_hits(search.search("限流", 2))
    assert isinstance(hits_payload["hits"][0]["score"], float)

    report_payload = serialize_report(ingest.sync())
    assert report_payload["failed_doc_ids"] == []
