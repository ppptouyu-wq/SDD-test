"""飞书机器人推送模块。

契约来源：specs/design.md §4.3 推送层接口契约 / specs/tasks.md Task 8
    lark_bot.send(report, chat_id) -> bool

验收标准（tasks.md Task 8）：
- [x] send() 函数签名符合 design.md §4.3 的接口定义
- [x] 发送的消息为 Markdown 格式
- [x] 发送失败时重试2次，仍失败则返回 False + 错误日志
- [x] 使用 Mock webhook 的单元测试通过

职责边界（design.md §2）：不做数据处理；不做日报生成；不做数据采集。
"""

from __future__ import annotations

from typing import Callable

import httpx

from shared.config import LarkConfig
from shared.logger import get_logger
from shared.models import DailyReport

from collector.lark_auth import LarkTokenManager

logger = get_logger("notifier.lark_bot")


def send(
    report: DailyReport,
    chat_id: str,
    *,
    config: LarkConfig,
    client: httpx.Client | None = None,
    token_manager: LarkTokenManager | None = None,
    transport: Callable[[httpx.Client, dict], httpx.Response] | None = None,
) -> bool:
    """通过飞书机器人把 Markdown 日报推送出去。失败重试 2 次。

    目标 ID 的类型由 `config.receive_id_type` 决定（默认 `chat_id` 推群）。
    若飞书应用没有群管理权限，可设为 `open_id` 走单聊 —— 实测单聊只需
    `im:message:send_as_bot`，不需要 `im:chat:create`。
    """
    if not chat_id:
        logger.error("飞书推送跳过：未配置目标 ID", extra={"source": "lark_bot"})
        return False

    owns_client = client is None
    if client is None:
        client = httpx.Client(base_url=config.api_base, timeout=config.timeout)
    tokens = token_manager or LarkTokenManager(client, config.app_id, config.app_secret)

    receive_id_type = config.receive_id_type or "chat_id"
    body = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": _markdown_content(report),
    }

    last_error: Exception | None = None
    try:
        for attempt in range(config.max_retries + 1):
            try:
                if transport is not None:
                    response = transport(client, body)
                else:
                    response = client.post(
                        "/im/v1/messages",
                        params={"receive_id_type": receive_id_type},
                        json=body,
                        headers={"Authorization": f"Bearer {tokens.get_token()}"},
                    )
                if response.status_code >= 400:
                    raise httpx.HTTPStatusError(
                        f"飞书推送 HTTP {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                data = response.json()
                if data.get("code") not in (0, None):
                    raise httpx.HTTPError(f"飞书推送错误：{data.get('msg')}")
                logger.info(
                    "飞书推送成功",
                    extra={
                        "source": "lark_bot",
                        "receive_id_type": receive_id_type,
                        "target": chat_id,
                        "attempt": attempt + 1,
                    },
                )
                return True
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                logger.error(
                    "飞书推送失败",
                    extra={
                        "source": "lark_bot",
                        "attempt": attempt + 1,
                        "max_retries": config.max_retries,
                        "error": str(exc),
                    },
                )
    finally:
        if owns_client:
            client.close()

    logger.error("飞书推送最终失败", extra={"source": "lark_bot", "error": str(last_error)})
    return False


def _markdown_content(report: DailyReport) -> str:
    """飞书 text 消息体（JSON 字符串），内容为 Markdown 格式日报。"""
    import json

    return json.dumps({"text": report.markdown}, ensure_ascii=False)
