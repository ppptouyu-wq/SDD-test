"""飞书任务采集模块。

契约来源：specs/design.md §4.1 采集层接口契约 / specs/tasks.md Task 4
    lark_task.collect(since, until, *, config, ...) -> CollectResult[TaskRecord]

验收标准（tasks.md Task 4，v1.16 重写）：
- [x] 用**用户身份** `user_access_token` 调 `GET /task/v2/tasks`（`type=my_tasks`）
- [x] 没有用户授权时明确失败，而不是静默返回 0 条
- [x] 返回的每个 TaskRecord 含 assignee/title/status/created_at/due_at/completed_at/updated_at
- [x] 只保留 created/updated/completed 任一落在 [since, until] 内的任务
- [x] 飞书 Token 过期时自动刷新后重试1次；HTTP 超时重试 3 次后标记失败
- [x] 使用 Mock 数据的单元测试全部通过

职责边界（design.md §2）：只负责"从外部 API 获取原始数据"。

**为什么 v1.16 整个重写**：旧版有三处是照着想象的接口写的 ——
① 传 `updated_since` / `updated_until`（该接口根本没有这两个参数）；
② 从 `status_from` / `status_to` 取状态流转（接口只返回**当前**状态）；
③ 用 `tenant_access_token` 调用（官方文档明确只接受 `user_access_token`）。
真跑的结果就是**恒返回 code=0 + 0 条**：不报错，但永远是空的。
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Callable, Protocol

import httpx

from shared.config import LarkConfig
from shared.errors import CollectorError
from shared.logger import get_logger
from shared.models import CollectResult, TaskRecord

logger = get_logger("collector.lark_task")

# 飞书 Token 失效错误码
TOKEN_INVALID_CODES = {99991663, 99991664, 99991661, 99991668}


class _TokenSource(Protocol):
    """能提供 `user_access_token` 的对象（`UserTokenManager` 满足该协议）。"""

    def get_token(self) -> str: ...

    def invalidate(self) -> None: ...


def _epoch_ms_to_datetime(value: object) -> datetime | None:
    """飞书时间戳是毫秒字符串；"0" / 空值表示"没有这个时间"。"""
    text = str(value or "").strip()
    if not text or text == "0":
        return None
    try:
        millis = float(text)
    except ValueError:
        return None
    if millis <= 0:
        return None
    # 用本机时区还原成 naive datetime —— 与 main.py 里 since/until 的构造方式一致，
    # 否则 aware / naive 混用会在比较时直接抛 TypeError。
    return datetime.fromtimestamp(millis / 1000.0)


def _member_id(raw: object) -> str:
    if isinstance(raw, dict):
        return str(raw.get("id") or "")
    return ""


def _first_assignee(item: dict) -> str:
    for key in ("members", "assignees"):
        members = item.get(key) or []
        if isinstance(members, list) and members:
            for member in members:
                # 只认 user 类型：chat/部门成员不能当"负责人"
                if isinstance(member, dict) and member.get("type") not in (None, "user"):
                    continue
                member_id = _member_id(member)
                if member_id:
                    return member_id
    return _member_id(item.get("creator")) or "unassigned"


def _due_at(item: dict) -> datetime | None:
    due = item.get("due")
    if isinstance(due, dict):
        return _epoch_ms_to_datetime(due.get("timestamp"))
    return None


def _to_record(item: dict) -> TaskRecord:
    return TaskRecord(
        assignee=_first_assignee(item),
        title=str(item.get("summary", "") or ""),
        status=str(item.get("status", "") or "todo"),
        created_at=_epoch_ms_to_datetime(item.get("created_at")),
        due_at=_due_at(item),
        completed_at=_epoch_ms_to_datetime(item.get("completed_at")),
        updated_at=_epoch_ms_to_datetime(item.get("updated_at")) or datetime.now(),
    )


def _touched_within(record: TaskRecord, since: datetime, until: datetime) -> bool:
    """创建 / 更新 / 完成 任一落在窗口内，就算"这一天有动静"。"""
    for moment in (record.created_at, record.completed_at, record.updated_at):
        if moment is not None and since <= moment <= until:
            return True
    return False


def collect(
    since: datetime,
    until: datetime,
    *,
    config: LarkConfig,
    client: httpx.Client | None = None,
    token_manager: _TokenSource | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> CollectResult[TaskRecord]:
    """采集「我负责的」任务中 [since, until] 内有动静的那些。

    没有 `token_manager`（即用户没授权过）时**直接失败**并说明怎么授权：
    应用身份调这个接口不报错、只返回空，若在这里安静地返回 0 条，
    日报会写"今日无任务"，而用户明明在飞书里建了任务 —— 这是最坏的错法。
    """
    if token_manager is None:
        return CollectResult.fail(
            "未授权读取个人任务：飞书任务接口只接受 user_access_token，"
            "请先执行 python main.py --lark-auth 完成一次性授权"
        )

    owns_client = client is None
    if client is None:
        client = httpx.Client(base_url=config.api_base, timeout=config.timeout)

    records: list[TaskRecord] = []
    try:
        page_token: str | None = None
        seen = 0
        while True:
            params: dict[str, object] = {"page_size": 50, "type": "my_tasks"}
            if page_token:
                params["page_token"] = page_token

            data = _request_with_auth_retry(
                client, token_manager, "/task/v2/tasks", params, config, sleep
            )

            payload = data.get("data") or {}
            items = payload.get("items") or []
            seen += len(items)
            for item in items:
                record = _to_record(item)
                if _touched_within(record, since, until):
                    records.append(record)

            page_token = payload.get("page_token") or None
            if not page_token or not payload.get("has_more"):
                break

        logger.info(
            "飞书任务采集完成",
            extra={"source": "lark_task", "count": len(records), "scanned": seen},
        )
        return CollectResult.ok(records)
    except CollectorError as exc:
        logger.error("飞书任务采集失败", extra={"source": "lark_task", "error": exc.message})
        return CollectResult.fail(exc.message)
    finally:
        if owns_client:
            client.close()


def _request_with_auth_retry(
    client: httpx.Client,
    tokens: _TokenSource,
    path: str,
    params: dict[str, object],
    config: LarkConfig,
    sleep: Callable[[float], None],
) -> dict:
    """带"Token 过期自动刷新重试 1 次"和"超时重试 3 次"的请求。"""
    token_retried = False
    last_error: Exception | None = None

    for attempt in range(config.max_retries + 1):
        try:
            response = client.get(
                path, params=params, headers={"Authorization": f"Bearer {tokens.get_token()}"}
            )
            if response.status_code >= 400:
                raise CollectorError(
                    f"飞书任务 API 返回 HTTP {response.status_code}", source="lark_task"
                )
            data = response.json()

            # Token 失效：刷新后重试 1 次
            if data.get("code") in TOKEN_INVALID_CODES:
                if not token_retried:
                    token_retried = True
                    logger.error(
                        "飞书 Token 已过期，刷新后重试",
                        extra={"source": "lark_task", "code": data.get("code")},
                    )
                    tokens.invalidate()
                    continue
                raise CollectorError(
                    f"飞书 Token 刷新后仍无效：{data.get('msg')}", source="lark_task"
                )

            if data.get("code") not in (0, None):
                raise CollectorError(
                    f"飞书任务 API 错误：{data.get('msg')}", source="lark_task"
                )
            return data

        except (httpx.HTTPError, CollectorError) as exc:
            last_error = exc
            if attempt < config.max_retries:
                logger.error(
                    "飞书任务采集失败，准备重试",
                    extra={"source": "lark_task", "attempt": attempt + 1, "error": str(exc)},
                )
                if config.timeout:
                    sleep(0.0)  # 重试间隔由调用方注入的 sleep 控制

    raise CollectorError(
        f"飞书任务 API 重试 {config.max_retries} 次后仍失败：{last_error}", source="lark_task"
    )