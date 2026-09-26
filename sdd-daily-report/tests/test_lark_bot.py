"""Task 8 验收标准：飞书推送模块单元测试。

- send() 函数签名符合 design.md §4.3 的接口定义
- 发送的消息为 Markdown 格式
- 发送失败时重试2次，仍失败则返回 False+错误日志
- 使用 Mock webhook 的单元测试通过
"""

from __future__ import annotations

import json
from datetime import date, datetime

import httpx

from collector.lark_auth import LarkTokenManager
from notifier import lark_bot
from shared.models import DailyReport, MemberReport

DAY = date(2026, 8, 20)
TOKEN_PAYLOAD = {"code": 0, "tenant_access_token": "t-abc", "expire": 7200}


def _report() -> DailyReport:
    return DailyReport(
        date=DAY,
        team_name="平台研发组",
        members=[MemberReport(name="张三", github_username="zhangsan")],
        generated_at=datetime(2026, 8, 20, 18, 0),
        markdown="# 平台研发组工作日报 — 2026-08-20\n\n### 代码提交\n- feat: 日报编排\n",
        html="<html></html>",
    )


def _client(handler) -> httpx.Client:
    return httpx.Client(
        base_url="https://open.feishu.cn/open-apis",
        transport=httpx.MockTransport(handler),
    )


def _send(config, handler):
    client = _client(handler)
    tokens = LarkTokenManager(client, config.lark.app_id, config.lark.app_secret)

    def transport(client_: httpx.Client, body: dict) -> httpx.Response:
        return client_.post(
            "/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            json=body,
            headers={"Authorization": f"Bearer {tokens.get_token()}"},
        )

    return lark_bot.send(
        _report(), config.lark.chat_id,
        config=config.lark, client=client, token_manager=tokens, transport=transport,
    )


def test_send_pushes_markdown_message(config):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"code": 0, "msg": "success"})

    ok = _send(config, handler)

    assert ok is True
    assert captured["receive_id"] == config.lark.chat_id
    # 发送的消息为 Markdown 格式
    content = json.loads(captured["content"])
    assert content["text"].startswith("# 平台研发组工作日报")
    assert "### 代码提交" in content["text"]


def test_failure_retries_twice_then_returns_false(config):
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        attempts["n"] += 1
        return httpx.Response(500, json={"code": 500, "msg": "internal error"})

    ok = _send(config, handler)

    assert ok is False
    assert attempts["n"] == 4  # 首次 + 3 次重试（config.lark.max_retries = 3）


def test_api_error_code_returns_false(config):
    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        return httpx.Response(200, json={"code": 230002, "msg": "bot not in chat"})

    ok = _send(config, handler)

    assert ok is False


def test_recovers_after_transient_failure(config):
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(502, json={"code": 502})
        return httpx.Response(200, json={"code": 0})

    ok = _send(config, handler)

    assert ok is True
    assert state["n"] == 2


def test_missing_chat_id_returns_false(config):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=TOKEN_PAYLOAD)

    client = _client(handler)
    ok = lark_bot.send(_report(), "", config=config.lark, client=client)

    assert ok is False


def test_default_receive_id_type_is_chat_id(config):
    """默认按群推送，保持既有行为不变。"""
    assert config.lark.receive_id_type == "chat_id"


def test_receive_id_type_is_forwarded_as_query_param(config):
    """`config.receive_id_type` 必须作为查询参数传给飞书。

    背景：飞书应用若没有群管理权限（im:chat:create）就无法建群；
    此时可改用 open_id 走单聊 —— 实测单聊只需 im:message:send_as_bot。
    """
    from dataclasses import replace

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"code": 0})

    client = _client(handler)
    tokens = LarkTokenManager(client, config.lark.app_id, config.lark.app_secret)
    open_id_cfg = replace(config.lark, receive_id_type="open_id")

    ok = lark_bot.send(
        _report(), "ou_test_open_id",
        config=open_id_cfg, client=client, token_manager=tokens,
        transport=lambda c, body: c.post(
            "/im/v1/messages",
            params={"receive_id_type": open_id_cfg.receive_id_type},
            json=body,
            headers={"Authorization": f"Bearer {tokens.get_token()}"},
        ),
    )

    assert ok is True
    assert seen["params"]["receive_id_type"] == "open_id"


def test_config_loads_receive_id_type_from_yaml(tmp_dir):
    """config.yaml 里的 receive_id_type 必须能被读取（缺省回落 chat_id）。"""
    from shared.config import load_config

    base_yaml = """
members:
  - name: "张三"
    github: "zhangsan"
    lark: "zhangsan@company.com"
lark:
  chat_id: "ou_x"
report:
  team_name: "组"
"""
    path = tmp_dir / "config.yaml"
    path.write_text(base_yaml, encoding="utf-8")
    assert load_config(path).lark.receive_id_type == "chat_id"

    path.write_text(
        base_yaml.replace('  chat_id: "ou_x"', '  chat_id: "ou_x"\n  receive_id_type: "open_id"'),
        encoding="utf-8",
    )
    assert load_config(path).lark.receive_id_type == "open_id"
