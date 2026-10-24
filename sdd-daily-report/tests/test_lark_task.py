"""Task 4 验收标准：飞书任务采集模块单元测试（v1.16 重写）。

- [x] collect() 用**用户身份** user_access_token 调 `/task/v2/tasks`（`type=my_tasks`）
- [x] 没有用户授权时明确失败，而不是静默返回 0 条
- [x] 每个 TaskRecord 含 assignee/title/status/created_at/due_at/completed_at/updated_at
- [x] 只保留 created/updated/completed 任一落在窗口内的任务
- [x] 飞书 Token 过期时自动刷新后重试1次；超时重试3次后标记失败

时间戳一律用"本机时间 → 毫秒"现场算：飞书返回的是毫秒时间戳，采集层用
`datetime.fromtimestamp` 还原成本机时区，测试若写死 UTC 时间串，换个时区的机器跑就会
跨出 [0:00, 23:59] 的窗口而假失败。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx

from collector import lark_task
from shared.models import CollectResult, TaskRecord

DAY = datetime(2026, 8, 20)
SINCE = DAY.replace(hour=0, minute=0, second=0)
UNTIL = DAY.replace(hour=23, minute=59, second=59)


def _ms(moment: datetime) -> str:
    """本机时间 → 飞书用的毫秒时间戳字符串。"""
    return str(int(moment.timestamp() * 1000))


def _item(
    *,
    summary: str = "日报模板评审",
    status: str = "done",
    created_at: datetime | None = None,
    completed_at: datetime | None = None,
    updated_at: datetime | None = None,
    assignee: str = "ou_lisi",
) -> dict:
    created = created_at if created_at is not None else DAY.replace(hour=9, minute=40)
    updated = updated_at if updated_at is not None else DAY.replace(hour=16, minute=30)
    # 已完成的任务必然带完成时间；测试里不显式给就取"更新时间"
    if completed_at is None and status == "done":
        completed_at = updated
    completed = completed_at
    return {
        "guid": "83912691-2e43-47fc-94a4-d512e03984fa",
        "summary": summary,
        "status": status,
        "created_at": _ms(created),
        "updated_at": _ms(updated),
        # 飞书未完成时 completed_at 是字符串 "0"
        "completed_at": _ms(completed) if completed else "0",
        "due": {"timestamp": _ms(DAY.replace(hour=18, minute=0)), "is_all_day": False},
        "members": [{"id": assignee, "type": "user", "role": "assignee"}],
        "creator": {"id": "ou_creator", "type": "user", "role": "creator"},
    }


class _UserTokens:
    """假的用户身份 Token 源（满足 `_TokenSource` 协议）。"""

    def __init__(self, value: str = "u-token") -> None:
        self.value = value
        self.refresh_count = 0

    def get_token(self) -> str:
        return self.value

    def invalidate(self) -> None:
        self.refresh_count += 1
        self.value = f"u-token-{self.refresh_count + 1}"


def _client(handler) -> httpx.Client:
    return httpx.Client(
        base_url="https://open.feishu.cn/open-apis",
        transport=httpx.MockTransport(handler),
    )


def test_collect_returns_real_task_fields(config, no_sleep):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "data": {"items": [_item()]}})

    client = _client(handler)
    result = lark_task.collect(
        SINCE, UNTIL,
        config=config.lark, client=client, token_manager=_UserTokens(), sleep=no_sleep,
    )

    assert isinstance(result, CollectResult)
    assert result.success is True
    assert len(result.records) == 1

    record = result.records[0]
    assert isinstance(record, TaskRecord)
    assert record.assignee == "ou_lisi"
    assert record.title == "日报模板评审"
    assert record.status == "done"
    assert record.done is True
    assert record.completed_at is not None and record.completed_at.hour == 16
    assert record.created_at is not None and record.created_at.hour == 9
    assert record.due_at is not None and record.due_at.hour == 18
    assert isinstance(record.updated_at, datetime)


def test_request_uses_user_token_and_my_tasks(config, no_sleep):
    """接口只认 user_access_token，且只列「我负责的」任务 —— 两个都不能少。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"code": 0, "data": {"items": [_item()]}})

    client = _client(handler)
    lark_task.collect(
        SINCE, UNTIL,
        config=config.lark, client=client, token_manager=_UserTokens("u-abc"), sleep=no_sleep,
    )

    assert seen["auth"] == "Bearer u-abc"
    assert seen["params"]["type"] == "my_tasks"
    # 旧版臆造的窗口参数，该接口并不存在
    assert "updated_since" not in seen["params"]
    assert "updated_until" not in seen["params"]


def test_tasks_outside_the_day_are_dropped(config, no_sleep):
    """接口不按日期过滤，窗口过滤必须在采集层做，否则会把陈年旧任务算进今天。"""
    old = DAY - timedelta(days=5)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "items": [
                        _item(summary="今天建的"),
                        _item(summary="五天前的", created_at=old, updated_at=old),
                    ]
                },
            },
        )

    client = _client(handler)
    result = lark_task.collect(
        SINCE, UNTIL,
        config=config.lark, client=client, token_manager=_UserTokens(), sleep=no_sleep,
    )

    assert result.success is True
    assert [record.title for record in result.records] == ["今天建的"]


def test_pagination_follows_page_token(config, no_sleep):
    pages = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        pages["n"] += 1
        if pages["n"] == 1:
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"items": [_item(summary="第一页")], "has_more": True, "page_token": "p2"},
                },
            )
        assert request.url.params.get("page_token") == "p2"
        return httpx.Response(
            200,
            json={"code": 0, "data": {"items": [_item(summary="第二页")], "has_more": False}},
        )

    client = _client(handler)
    result = lark_task.collect(
        SINCE, UNTIL,
        config=config.lark, client=client, token_manager=_UserTokens(), sleep=no_sleep,
    )

    assert result.success is True
    assert [record.title for record in result.records] == ["第一页", "第二页"]


def test_token_is_refreshed_and_request_retried_once(config, no_sleep):
    """验收标准：飞书 Token 过期时自动刷新后重试1次。"""
    tokens = _UserTokens()
    expired_seen = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if expired_seen["n"] == 0:
            expired_seen["n"] += 1
            return httpx.Response(200, json={"code": 99991663, "msg": "token expired"})
        return httpx.Response(200, json={"code": 0, "data": {"items": [_item()]}})

    client = _client(handler)
    result = lark_task.collect(
        SINCE, UNTIL,
        config=config.lark, client=client, token_manager=tokens, sleep=no_sleep,
    )

    assert result.success is True
    assert len(result.records) == 1
    assert tokens.refresh_count == 1, "只应刷新一次"


def test_persistently_expired_token_fails_after_one_retry(config, no_sleep):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 99991663, "msg": "token expired"})

    client = _client(handler)
    result = lark_task.collect(
        SINCE, UNTIL,
        config=config.lark, client=client, token_manager=_UserTokens(), sleep=no_sleep,
    )

    assert result.success is False
    assert "Token" in result.error_message


def test_timeout_retries_three_times_then_marks_failure(config, no_sleep):
    attempts = {"api": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["api"] += 1
        raise httpx.ConnectTimeout("连接超时")

    client = _client(handler)
    result = lark_task.collect(
        SINCE, UNTIL,
        config=config.lark, client=client, token_manager=_UserTokens(), sleep=no_sleep,
    )

    assert attempts["api"] == 4  # 首次 + 3 次重试
    assert result.success is False
    assert result.records == []
    assert "重试 3 次后仍失败" in result.error_message


def test_api_error_code_marks_failure(config, no_sleep):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 10002, "msg": "permission denied"})

    client = _client(handler)
    result = lark_task.collect(
        SINCE, UNTIL,
        config=config.lark, client=client, token_manager=_UserTokens(), sleep=no_sleep,
    )

    assert result.success is False
    assert "permission denied" in result.error_message


def test_without_user_token_it_fails_instead_of_returning_zero(config, no_sleep):
    """没有用户授权时必须报错。

    这是本项目最容易犯的错：应用身份调该接口不报错、只返回 0 条，
    于是"没授权"被渲染成"今日无任务"，用户明明在飞书里建了任务。
    """
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不该被调用
        called["n"] += 1
        return httpx.Response(200, json={"code": 0, "data": {"items": []}})

    client = _client(handler)
    result = lark_task.collect(
        SINCE, UNTIL, config=config.lark, client=client, token_manager=None, sleep=no_sleep
    )

    assert result.success is False
    assert "user_access_token" in result.error_message
    assert "--lark-auth" in result.error_message
    assert called["n"] == 0, "没有授权时不应发出任何请求"