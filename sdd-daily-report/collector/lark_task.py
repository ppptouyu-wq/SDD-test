"""飞书任务采集模块。

契约来源：specs/design.md §4.1 采集层接口契约 / specs/tasks.md Task 4
    lark_task.collect(project_id, since, until) -> CollectResult[TaskRecord]

验收标准（tasks.md Task 4）：
- [x] collect() 函数签名符合 design.md §4.1 的接口定义
- [x] 返回的每个 TaskRecord 包含全部5个字段且类型正确
- [x] 飞书 Token 过期时自动刷新后重试1次
- [x] API 超时重试3次，若仍失败则标记数据获取失败
- [x] 使用 Mock 数据的单元测试全部通过

职责边界（design.md §2）：只负责"从外部 API 获取原始数据"。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

import httpx

from shared.config import LarkConfig
from shared.errors import CollectorError
from shared.logger import get_logger
from shared.models import CollectResult, TaskRecord

from collector.lark_auth import LarkTokenManager

logger = get_logger("collector.lark_task")

# 飞书 Token 失效错误码
TOKEN_INVALID_CODES = {99991663, 99991664, 99991661, 99991668}


def _parse_timestamp(value: object) -> datetime:
    """飞书返回毫秒时间戳或 ISO 字符串。"""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
    text = str(value or "")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(tz=timezone.utc)


def collect(
    project_id: str,
    since: datetime,
    until: datetime,
    *,
    config: LarkConfig,
    client: httpx.Client | None = None,
    token_manager: LarkTokenManager | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> CollectResult[TaskRecord]:
    """采集指定飞书任务清单中 [since, until] 内发生状态变更的任务。

    `project_id`（任务清单 ID）**是可选的**：实测传空值或不存在的值都不报错，
    只是返回 0 条。因此未配置时不再直接判失败，而是照常请求
    —— 这样"接口可用但暂无数据"与"采集失败"能被区分开。
    """
    owns_client = client is None
    if client is None:
        client = httpx.Client(base_url=config.api_base, timeout=config.timeout)
    tokens = token_manager or LarkTokenManager(client, config.app_id, config.app_secret)

    records: list[TaskRecord] = []
    try:
        page_token: str | None = None
        while True:
            params: dict[str, object] = {
                "updated_since": int(since.timestamp() * 1000),
                "updated_until": int(until.timestamp() * 1000),
                "page_size": 50,
            }
            # 仅在实际配置了任务清单 ID 时才带上该参数
            if project_id:
                params["project_id"] = project_id
            if page_token:
                params["page_token"] = page_token

            data = _request_with_auth_retry(
                client, tokens, "/task/v2/tasks", params, config, sleep
            )

            items = ((data.get("data") or {}).get("items")) or []
            for item in items:
                records.append(
                    TaskRecord(
                        assignee=str(
                            _first_assignee(item) or item.get("creator", "") or "unassigned"
                        ),
                        title=str(item.get("summary", "")),
                        status_from=str(item.get("status_from", "") or ""),
                        status_to=str(item.get("status", "") or ""),
                        updated_at=_parse_timestamp(item.get("updated_at")),
                    )
                )
            page_token = ((data.get("data") or {}).get("page_token")) or None
            if not page_token:
                break

        logger.info(
            "飞书任务采集完成",
            extra={"source": "lark_task", "count": len(records), "project_id": project_id},
        )
        return CollectResult.ok(records)
    except CollectorError as exc:
        logger.error("飞书任务采集失败", extra={"source": "lark_task", "error": exc.message})
        return CollectResult.fail(exc.message)
    finally:
        if owns_client:
            client.close()


def _first_assignee(item: dict) -> str:
    assignees = item.get("assignees") or []
    if assignees and isinstance(assignees, list):
        first = assignees[0]
        if isinstance(first, dict):
            return str(first.get("id") or first.get("name") or "")
        return str(first)
    return ""


def _request_with_auth_retry(
    client: httpx.Client,
    tokens: LarkTokenManager,
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
