"""Task 5 验收标准：飞书消息采集模块单元测试。

- collect() 函数签名符合 design.md §4.1 的接口定义
- 返回的每个 MessageRecord 包含全部4个字段且类型正确
- 关键词过滤正确，只返回包含指定关键词的消息
- 敏感关键词（薪资、绩效等）被正确过滤，不出现在结果中
- 飞书 Token 过期时自动刷新后重试1次
- 使用 Mock 数据的单元测试全部通过
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from collector import lark_msg
from collector.lark_auth import LarkTokenManager
from shared.models import CollectResult, MessageRecord

SINCE = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)
UNTIL = datetime(2026, 8, 20, 23, 59, tzinfo=timezone.utc)

TOKEN_PAYLOAD = {"code": 0, "tenant_access_token": "t-abc", "expire": 7200}


def _message(sender: str, text: str) -> dict:
    return {
        "message_id": f"om_{abs(hash(text)) % 10000}",
        "sender": {"id": sender, "sender_type": "user"},
        "body": {"content": json.dumps({"text": text}, ensure_ascii=False)},
        "create_time": "1755676800000",
        "chat_name": "平台研发组",
    }


def _client(handler) -> httpx.Client:
    return httpx.Client(
        base_url="https://open.feishu.cn/open-apis",
        transport=httpx.MockTransport(handler),
    )


def _tokens(client, config) -> LarkTokenManager:
    return LarkTokenManager(client, config.lark.app_id, config.lark.app_secret)


def _collect(config, handler, no_sleep, keywords=None, sensitive=None):
    client = _client(handler)
    return lark_msg.collect(
        config.lark.chat_id,
        keywords if keywords is not None else config.lark.keywords,
        SINCE, UNTIL,
        config=config.lark,
        sensitive_keywords=sensitive if sensitive is not None else config.report.sensitive_keywords,
        client=client,
        token_manager=_tokens(client, config),
        sleep=no_sleep,
    )


def test_collect_returns_four_field_message_records(config, no_sleep):
    item = _message("zhangsan@company.com", "这个项目的需求我下午确认")

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        return httpx.Response(200, json={"code": 0, "data": {"items": [item]}})

    result = _collect(config, handler, no_sleep)

    assert isinstance(result, CollectResult)
    assert result.success is True
    assert len(result.records) == 1

    record = result.records[0]
    assert isinstance(record, MessageRecord)
    assert record.sender == "zhangsan@company.com"
    assert "需求" in record.content
    assert isinstance(record.timestamp, datetime)
    assert record.chat_name == "平台研发组"


def test_keyword_filtering_only_keeps_matching_messages(config, no_sleep):
    """验收标准：关键词过滤正确，只返回包含指定关键词的消息。"""
    items = [
        _message("zhangsan@company.com", "这个项目的需求我下午确认"),   # 命中"项目"+"需求"
        _message("lisi@company.com", "中午一起吃饭吗"),                  # 不命中
        _message("wangwu@company.com", "线上有个 bug 需要发布修复"),      # 命中"bug"+"发布"
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        return httpx.Response(200, json={"code": 0, "data": {"items": items}})

    result = _collect(config, handler, no_sleep)

    assert [r.content for r in result.records] == [
        "这个项目的需求我下午确认",
        "线上有个 bug 需要发布修复",
    ]


def test_sensitive_keywords_are_filtered_out(config, no_sleep):
    """验收标准：敏感关键词（薪资、绩效等）被正确过滤，不出现在结果中。

    安全约束来源：design.md §6.2
    """
    items = [
        _message("zhangsan@company.com", "项目的需求评审通过了"),
        _message("lisi@company.com", "本月绩效沟通安排在下周"),
        _message("wangwu@company.com", "发布计划已同步，顺便提一下薪资调整"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        return httpx.Response(200, json={"code": 0, "data": {"items": items}})

    result = _collect(config, handler, no_sleep)

    contents = [r.content for r in result.records]
    assert contents == ["项目的需求评审通过了"]
    assert all("绩效" not in c and "薪资" not in c for c in contents)


def test_token_expiry_refreshes_and_retries_once(config, no_sleep):
    """验收标准：飞书 Token 过期时自动刷新后重试1次。"""
    issued = {"n": 0}
    expired = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            issued["n"] += 1
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": f"t-{issued['n']}", "expire": 7200}
            )
        if expired["n"] == 0:
            expired["n"] += 1
            return httpx.Response(200, json={"code": 99991663, "msg": "token expired"})
        return httpx.Response(
            200,
            json={"code": 0, "data": {"items": [_message("zhangsan@company.com", "项目进展同步")]}},
        )

    result = _collect(config, handler, no_sleep)

    assert result.success is True
    assert len(result.records) == 1
    assert issued["n"] == 2


def test_timeout_retries_then_marks_failure(config, no_sleep):
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        attempts["n"] += 1
        raise httpx.ReadTimeout("超时")

    result = _collect(config, handler, no_sleep)

    assert attempts["n"] == 4
    assert result.success is False
    assert "重试 3 次后仍失败" in result.error_message


def test_missing_chat_id_returns_failure(config, no_sleep):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=TOKEN_PAYLOAD)

    client = _client(handler)
    result = lark_msg.collect(
        "", ["项目"], SINCE, UNTIL,
        config=config.lark, client=client, token_manager=_tokens(client, config),
        sleep=no_sleep,
    )

    assert result.success is False
    assert "chat_id" in result.error_message


def test_rich_text_post_is_flattened(config, no_sleep):
    """富文本（post）消息应拍平为纯文本后参与过滤。"""
    item = {
        "sender": {"id": "zhangsan@company.com"},
        "body": {
            "content": json.dumps(
                {"content": [[{"tag": "text", "text": "项目需求"}]], "title": ""},
                ensure_ascii=False,
            )
        },
        "create_time": "1755676800000",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        return httpx.Response(200, json={"code": 0, "data": {"items": [item]}})

    result = _collect(config, handler, no_sleep)

    assert len(result.records) == 1
    assert "项目需求" in result.records[0].content


def test_helper_functions_for_keyword_and_sensitive_matching():
    assert lark_msg.match_keywords("项目进展", ["项目"]) is True
    assert lark_msg.match_keywords("中午吃饭", ["项目"]) is False
    assert lark_msg.match_keywords("任意内容", []) is True
    assert lark_msg.is_sensitive("本月绩效沟通", ["绩效"]) is True
    assert lark_msg.is_sensitive("项目进展", ["绩效"]) is False
    # 默认黑名单
    assert lark_msg.is_sensitive("关于薪资调整") is True


# ==================================================== 真实结构校准（实测）
# 以下三条依据真实飞书响应校准。两处最初都写错了，接真实群消息才暴露。


def test_sender_id_extracted_from_sender_id_field():
    """发送者标识应取 `sender.id`。

    实测飞书返回 `sender = {id, id_type, sender_type, tenant_key}`：
      - 真人消息 `id` 是 open_id，`sender_type='user'`
      - 机器人消息 `id` 是 app_id，`sender_type='app'`
    旧实现取错了字段，导致所有消息的 sender 恒为 "unknown"。
    """
    item = {
        "sender": {
            "id": "ou_abc123",
            "id_type": "open_id",
            "sender_type": "user",
            "tenant_key": "t1",
        }
    }
    assert lark_msg._sender_id(item) == "ou_abc123"


def test_sender_id_for_bot_message_is_app_id():
    item = {"sender": {"id": "cli_xxx", "id_type": "app_id", "sender_type": "app"}}
    assert lark_msg._sender_id(item) == "cli_xxx"


def test_sender_id_falls_back_to_unknown_when_empty():
    assert lark_msg._sender_id({"sender": {"id": ""}}) == "unknown"
    assert lark_msg._sender_id({}) == "unknown"


def test_system_messages_are_skipped(config, no_sleep):
    """`msg_type='system'` 的系统消息必须跳过。

    实测这类消息（入群提示、群名变更）的 sender.id 为空串，
    正文是模板字符串（如 `{from_user} invited {to_chatters} ...`），
    收进日报只会是噪声。
    """
    items = [
        {
            "msg_type": "system",
            "sender": {"id": "", "id_type": "", "sender_type": ""},
            "body": {"content": json.dumps(
                {"template": "{from_user} invited {to_chatters} to the group.",
                 "from_user": ["u1"]}, ensure_ascii=False)},
            "create_time": "1755676800000",
        },
        _message("zhangsan@company.com", "这个项目的需求我确认一下"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        return httpx.Response(200, json={"code": 0, "data": {"items": items}})

    result = _collect(config, handler, no_sleep)

    assert result.success is True
    assert len(result.records) == 1, "系统消息应被过滤掉"
    assert result.records[0].sender == "zhangsan@company.com"
    assert "invited" not in result.records[0].content


def test_config_auth_mode_defaults_to_app(tmp_dir):
    """群消息默认用应用身份 —— 实测该 scope 在应用身份下也能生效。

    保留 `auth_mode: user` 作为备用路径（部分租户该 scope 只对用户身份开放），
    但默认值必须是 app，否则用户每次都要手动 OAuth 授权。
    """
    from shared.config import load_config

    yaml_text = """
members:
  - name: "张三"
    github: "zhangsan"
    lark: "zhangsan@company.com"
lark:
  chat_id: "oc_x"
report:
  team_name: "组"
"""
    path = tmp_dir / "config.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    assert load_config(path).lark.auth_mode == "app"

    path.write_text(
        yaml_text.replace('  chat_id: "oc_x"', '  chat_id: "oc_x"\n  auth_mode: "user"'),
        encoding="utf-8",
    )
    assert load_config(path).lark.auth_mode == "user"
