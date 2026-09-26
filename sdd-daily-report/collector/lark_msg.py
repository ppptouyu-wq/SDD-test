"""飞书消息采集模块。

契约来源：specs/design.md §4.1 采集层接口契约 / specs/tasks.md Task 5
    lark_msg.collect(chat_id, keywords, since, until) -> CollectResult[MessageRecord]

验收标准（tasks.md Task 5）：
- [x] collect() 函数签名符合 design.md §4.1 的接口定义
- [x] 返回的每个 MessageRecord 包含全部4个字段且类型正确
- [x] 关键词过滤正确，只返回包含指定关键词的消息
- [x] 敏感关键词（薪资、绩效等）被正确过滤，不出现在结果中
- [x] 飞书 Token 过期时自动刷新后重试1次
- [x] 使用 Mock 数据的单元测试全部通过

安全约束来源：specs/design.md §6.2
    "对飞书消息中的敏感关键词（如薪资、绩效、裁员等）进行过滤（配置黑名单）"
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

import httpx

from shared.config import LarkConfig
from shared.errors import CollectorError
from shared.logger import get_logger
from shared.models import CollectResult, MessageRecord

from collector.lark_auth import (
    DEFAULT_TOKEN_FILE,
    LarkTokenManager,
    UserTokenManager,
)
from collector.lark_task import TOKEN_INVALID_CODES

logger = get_logger("collector.lark_msg")

DEFAULT_SENSITIVE_KEYWORDS = ["薪资", "绩效", "裁员", "期权", "工资"]


def _build_token_provider(client: httpx.Client, config: LarkConfig):
    """按配置选择 token 身份。

    群消息历史需要 `im:message.group_msg` scope。实测该 scope 在**应用身份**下
    也能生效（重新发布版本后 `GET /im/v1/messages` 返回 code=0），
    因此默认用应用身份，无需用户手动 OAuth 授权。

    `auth_mode: user` 保留为备用路径：若某些租户下该 scope 只对用户身份开放，
    可切换过去（需先跑 `python -m collector.lark_oauth` 完成一次授权）。
    """
    if getattr(config, "auth_mode", "app") == "user":
        return UserTokenManager(
            client,
            config.app_id,
            config.app_secret,
            token_file=getattr(config, "user_token_file", None) or DEFAULT_TOKEN_FILE,
        )
    return LarkTokenManager(client, config.app_id, config.app_secret)


def _parse_timestamp(value: object) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
    text = str(value or "")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(tz=timezone.utc)


def match_keywords(content: str, keywords: list[str]) -> bool:
    """关键词过滤：任一关键词命中即保留。keywords 为空时全部保留。"""
    if not keywords:
        return True
    return any(keyword in content for keyword in keywords)


def _sender_id(item: dict) -> str:
    """从消息体取发送者标识。

    飞书返回的 `sender` 结构为 `{id, id_type, sender_type, tenant_key}`：
      - 真人消息：`id` 是 open_id，`sender_type='user'`
      - 机器人消息：`id` 是 app_id，`sender_type='app'`

    实测确认取 `sender.id` 即可（旧实现误取了不存在的字段，导致恒为 "unknown"）。
    """
    sender = item.get("sender") or {}
    return str(sender.get("id") or "").strip() or "unknown"


def is_sensitive(content: str, sensitive_keywords: list[str] | None = None) -> bool:
    """敏感词过滤：命中任一敏感词即应剔除。"""
    blocked = sensitive_keywords if sensitive_keywords is not None else DEFAULT_SENSITIVE_KEYWORDS
    return any(word in content for word in blocked)


def collect(
    chat_id: str,
    keywords: list[str],
    since: datetime,
    until: datetime,
    *,
    config: LarkConfig,
    sensitive_keywords: list[str] | None = None,
    client: httpx.Client | None = None,
    token_manager: LarkTokenManager | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> CollectResult[MessageRecord]:
    """采集指定群在 [since, until] 内的消息，按关键词过滤并剔除敏感内容。"""
    if not chat_id:
        return CollectResult.fail("未配置飞书 chat_id")

    owns_client = client is None
    if client is None:
        client = httpx.Client(base_url=config.api_base, timeout=config.timeout)

    # 群聊消息历史**只支持用户身份**（im:message.group_msg 的权限类型是「用户身份」），
    # 应用身份即使开通该权限也会返回 230027。因此这里优先使用用户身份 token：
    #   - 传入了 token_provider → 用它（调用方注入，便于测试）
    #   - config.auth_mode == "user" → 用 UserTokenManager（从落盘文件读授权）
    #   - 否则退回应用身份（会失败，但错误信息会明确提示缺 scope，便于定位）
    tokens = token_manager or _build_token_provider(client, config)

    records: list[MessageRecord] = []
    try:
        page_token: str | None = None
        while True:
            params: dict[str, object] = {
                "container_id_type": "chat",
                "container_id": chat_id,
                "start_time": int(since.timestamp()),
                "end_time": int(until.timestamp()),
                "page_size": 50,
            }
            if page_token:
                params["page_token"] = page_token

            data = _request_with_auth_retry(client, tokens, "/im/v1/messages", params, config, sleep)

            items = ((data.get("data") or {}).get("items")) or []
            for item in items:
                # 跳过系统消息：入群提示、群名变更等 msg_type='system' 的消息
                # 没有真实发送者，正文是模板字符串（如 "{from_user} invited ..."），
                # 收进日报只会是噪声。实测确认其 sender.id 为空串。
                if str(item.get("msg_type", "")) == "system":
                    continue

                content = _extract_text(item)
                if not match_keywords(content, keywords):
                    continue
                if is_sensitive(content, sensitive_keywords):
                    logger.info(
                        "敏感消息已过滤",
                        extra={"source": "lark_msg", "chat_id": chat_id},
                    )
                    continue
                records.append(
                    MessageRecord(
                        sender=_sender_id(item),
                        content=content,
                        timestamp=_parse_timestamp(item.get("create_time")),
                        chat_name=str(item.get("chat_name", "") or chat_id),
                    )
                )
            page_token = ((data.get("data") or {}).get("page_token")) or None
            if not page_token:
                break

        logger.info(
            "飞书消息采集完成",
            extra={"source": "lark_msg", "count": len(records), "chat_id": chat_id},
        )
        return CollectResult.ok(records)
    except CollectorError as exc:
        logger.error("飞书消息采集失败", extra={"source": "lark_msg", "error": exc.message})
        return CollectResult.fail(exc.message)
    finally:
        if owns_client:
            client.close()


def _extract_text(item: dict) -> str:
    """从飞书消息体提取纯文本。"""
    body = item.get("body") or {}
    raw = body.get("content")
    if isinstance(raw, str):
        try:
            import json

            parsed = json.loads(raw)
            if isinstance(parsed, dict) and "text" in parsed:
                return str(parsed["text"])
            if isinstance(parsed, dict) and "content" in parsed:
                # 富文本 post：拍平所有 text 节点
                return _flatten_post(parsed)
            return raw
        except json.JSONDecodeError:
            return raw
    if isinstance(raw, dict):
        return str(raw.get("text") or "")
    return ""


def _flatten_post(node: object) -> str:
    """递归拍平富文本结构，收集所有 text 字段。"""
    parts: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "text" and isinstance(value, str):
                parts.append(value)
            else:
                parts.append(_flatten_post(value))
    elif isinstance(node, list):
        for value in node:
            parts.append(_flatten_post(value))
    return " ".join(p for p in parts if p)


def _request_with_auth_retry(
    client: httpx.Client,
    tokens: LarkTokenManager,
    path: str,
    params: dict[str, object],
    config: LarkConfig,
    sleep: Callable[[float], None],
) -> dict:
    token_retried = False
    last_error: Exception | None = None

    for attempt in range(config.max_retries + 1):
        try:
            response = client.get(
                path, params=params, headers={"Authorization": f"Bearer {tokens.get_token()}"}
            )
            # 飞书在 HTTP 400 时同样返回结构化 code/msg（如 230001 invalid container_id）。
            # 必须原样带出，否则上层只能看到"HTTP 400"，无法定位问题。
            try:
                body = response.json()
            except ValueError:
                body = {}
            if response.status_code >= 400 and not body.get("code"):
                raise CollectorError(
                    f"飞书消息 API 返回 HTTP {response.status_code}", source="lark_msg"
                )
            data = body

            if data.get("code") in TOKEN_INVALID_CODES:
                if not token_retried:
                    token_retried = True
                    logger.error(
                        "飞书 Token 已过期，刷新后重试",
                        extra={"source": "lark_msg", "code": data.get("code")},
                    )
                    tokens.invalidate()
                    continue
                raise CollectorError(
                    f"飞书 Token 刷新后仍无效：{data.get('msg')}", source="lark_msg"
                )

            if data.get("code") not in (0, None):
                raise CollectorError(f"飞书消息 API 错误：{data.get('msg')}", source="lark_msg")
            return data

        except (httpx.HTTPError, CollectorError) as exc:
            last_error = exc
            if attempt < config.max_retries:
                logger.error(
                    "飞书消息采集失败，准备重试",
                    extra={"source": "lark_msg", "attempt": attempt + 1, "error": str(exc)},
                )
                sleep(0.0)

    raise CollectorError(
        f"飞书消息 API 重试 {config.max_retries} 次后仍失败：{last_error}", source="lark_msg"
    )
