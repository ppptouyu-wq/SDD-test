"""Task 4 验收标准：飞书任务采集模块单元测试。

- collect() 函数签名符合 design.md §4.1 的接口定义
- 返回的每个 TaskRecord 包含全部5个字段且类型正确
- 飞书 Token 过期时自动刷新后重试1次
- API 超时重试3次，若仍失败则返回空列表+错误日志
- 使用 Mock 数据的单元测试全部通过
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from collector import lark_task
from collector.lark_auth import LarkTokenManager
from shared.models import CollectResult, TaskRecord

SINCE = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)
UNTIL = datetime(2026, 8, 20, 23, 59, tzinfo=timezone.utc)

TOKEN_PAYLOAD = {"code": 0, "tenant_access_token": "t-abc", "expire": 7200}

TASK_ITEM = {
    "summary": "日报模板评审",
    "status": "已完成",
    "status_from": "进行中",
    "updated_at": "2026-08-20T16:30:00+08:00",
    "assignees": [{"id": "lisi@company.com"}],
    "creator": "zhangsan@company.com",
}


def _client(handler) -> httpx.Client:
    return httpx.Client(
        base_url="https://open.feishu.cn/open-apis",
        transport=httpx.MockTransport(handler),
    )


def _tokens(client, config) -> LarkTokenManager:
    return LarkTokenManager(client, config.lark.app_id, config.lark.app_secret)


def test_collect_returns_five_field_task_records(config, no_sleep):
    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        return httpx.Response(200, json={"code": 0, "data": {"items": [TASK_ITEM]}})

    client = _client(handler)
    result = lark_task.collect(
        config.lark.project_id, SINCE, UNTIL,
        config=config.lark, client=client, token_manager=_tokens(client, config),
        sleep=no_sleep,
    )

    assert isinstance(result, CollectResult)
    assert result.success is True
    assert len(result.records) == 1

    record = result.records[0]
    assert isinstance(record, TaskRecord)
    assert record.assignee == "lisi@company.com"
    assert record.title == "日报模板评审"
    assert record.status_from == "进行中"
    assert record.status_to == "已完成"
    assert isinstance(record.updated_at, datetime)


def test_token_is_refreshed_and_request_retried_once(config, no_sleep):
    """验收标准：飞书 Token 过期时自动刷新后重试1次。"""
    tokens = {"issued": 0}
    expired_seen = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            tokens["issued"] += 1
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": f"t-{tokens['issued']}", "expire": 7200},
            )
        # 第一次业务请求返回 Token 失效
        if expired_seen["n"] == 0:
            expired_seen["n"] += 1
            return httpx.Response(200, json={"code": 99991663, "msg": "token expired"})
        return httpx.Response(200, json={"code": 0, "data": {"items": [TASK_ITEM]}})

    client = _client(handler)
    manager = _tokens(client, config)
    result = lark_task.collect(
        config.lark.project_id, SINCE, UNTIL,
        config=config.lark, client=client, token_manager=manager, sleep=no_sleep,
    )

    assert result.success is True
    assert len(result.records) == 1
    assert tokens["issued"] == 2  # 首次获取 + 过期后刷新
    assert manager.refresh_count == 2


def test_persistently_expired_token_fails_after_one_retry(config, no_sleep):
    tokens = {"issued": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            tokens["issued"] += 1
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "t-x", "expire": 7200}
            )
        return httpx.Response(200, json={"code": 99991663, "msg": "token expired"})

    client = _client(handler)
    result = lark_task.collect(
        config.lark.project_id, SINCE, UNTIL,
        config=config.lark, client=client, token_manager=_tokens(client, config),
        sleep=no_sleep,
    )

    assert result.success is False
    assert "Token" in result.error_message


def test_timeout_retries_three_times_then_marks_failure(config, no_sleep):
    attempts = {"api": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        attempts["api"] += 1
        raise httpx.ConnectTimeout("连接超时")

    client = _client(handler)
    result = lark_task.collect(
        config.lark.project_id, SINCE, UNTIL,
        config=config.lark, client=client, token_manager=_tokens(client, config),
        sleep=no_sleep,
    )

    assert attempts["api"] == 4  # 首次 + 3 次重试
    assert result.success is False
    assert result.records == []
    assert "重试 3 次后仍失败" in result.error_message


def test_empty_project_id_still_queries(config, no_sleep):
    """空 project_id 不应直接判失败 —— 实测该参数是可选的。

    早期实现遇到空 project_id 就返回 success=False，导致"接口可用但暂无数据"
    被误报成"采集失败"。现在会照常请求，只是不携带该参数。
    """
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"code": 0, "data": {"items": []}})

    client = _client(handler)
    result = lark_task.collect(
        "", SINCE, UNTIL,
        config=config.lark, client=client, token_manager=_tokens(client, config),
        sleep=no_sleep,
    )

    assert result.success is True
    assert result.records == []
    assert "project_id" not in seen["params"], "空值时不应把该参数发给飞书"


def test_api_error_code_marks_failure(config, no_sleep):
    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        return httpx.Response(200, json={"code": 10002, "msg": "permission denied"})

    client = _client(handler)
    result = lark_task.collect(
        config.lark.project_id, SINCE, UNTIL,
        config=config.lark, client=client, token_manager=_tokens(client, config),
        sleep=no_sleep,
    )

    assert result.success is False
    assert "permission denied" in result.error_message


def test_missing_credentials_raise_clear_error(config, no_sleep, monkeypatch):
    monkeypatch.delenv("LARK_APP_ID", raising=False)
    monkeypatch.delenv("LARK_APP_SECRET", raising=False)
    bare = config.lark.__class__(app_id=None, app_secret=None, project_id="p", chat_id="c")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "data": {"items": []}})

    client = _client(handler)
    result = lark_task.collect(
        "p", SINCE, UNTIL, config=bare, client=client, sleep=no_sleep
    )

    assert result.success is False
    assert "LARK_APP_ID" in result.error_message
