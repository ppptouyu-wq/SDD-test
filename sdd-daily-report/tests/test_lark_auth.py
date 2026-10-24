"""用户身份 Token 管理（collector/lark_auth.py）的单元测试。

v1.16 起「我负责的」任务只接受 `user_access_token`，所以这条链路本身成了
必需路径；v1.16.1 又补了两处踩出来的问题：

1. `/authen/v1/oidc/*` 的错误体是 `{code, error, error_description}`，**没有**
   `msg` 字段 —— 只读 `msg` 会把「code 无效」显示成「失败：None」。
2. 回调地址没登记时授权页会直接报错，于是补了 `adopt_token()` 让人把 API
   调试台拿到的令牌贴进来先用起来。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from collector.lark_auth import (
    DEFAULT_USER_SCOPES,
    TASK_READ_SCOPE,
    UserTokenManager,
)
from shared.errors import CollectorError


def json_response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(payload).encode("utf-8"),
    )


def make_client(handler) -> httpx.Client:
    return httpx.Client(
        base_url="https://open.feishu.cn/open-apis",
        transport=httpx.MockTransport(handler),
    )


def _manager(tmp_dir: Path, handler, *, clock=None) -> UserTokenManager:
    client = make_client(handler)
    kwargs = {"clock": clock} if clock is not None else {}
    return UserTokenManager(
        client,
        "cli_test",
        "secret_test",
        token_file=tmp_dir / "lark_user_token.json",
        **kwargs,
    )


def test_authorization_url_asks_for_both_scopes(tmp_dir: Path) -> None:
    manager = _manager(tmp_dir, lambda request: json_response({}))

    url = manager.authorization_url()

    assert url.startswith("https://open.feishu.cn/open-apis/authen/v1/authorize?")
    assert TASK_READ_SCOPE in DEFAULT_USER_SCOPES
    # scope 必须用 %20 之外的写法：飞书 OAuth 规定空格分隔，urlencode 后是 +
    assert "scope=" in url
    assert "task%3Atask%3Aread" in url
    assert "im%3Amessage.group_msg" in url


def test_authorization_url_accepts_narrower_scopes(tmp_dir: Path) -> None:
    manager = _manager(tmp_dir, lambda request: json_response({}))

    url = manager.authorization_url(scopes=TASK_READ_SCOPE)

    assert "scope=task%3Atask%3Aread" in url
    assert "im%3Amessage" not in url


def test_adopt_token_writes_a_reusable_file(tmp_dir: Path) -> None:
    manager = _manager(tmp_dir, lambda request: json_response({}), clock=lambda: 1000.0)

    manager.adopt_token("u-manual-token", expires_in=7200.0)

    path = tmp_dir / "lark_user_token.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["access_token"] == "u-manual-token"
    # expires_at 必须是 float：_load() 里做的是 float(...)，写 ISO 串会直接抛错
    assert payload["expires_at"] == pytest.approx(8200.0)
    assert payload["refresh_token"] is None

    reloaded = _manager(tmp_dir, lambda request: json_response({}), clock=lambda: 1000.0)
    assert reloaded.get_token() == "u-manual-token"


def test_adopt_token_rejects_an_empty_string(tmp_dir: Path) -> None:
    manager = _manager(tmp_dir, lambda request: json_response({}))

    with pytest.raises(CollectorError):
        manager.adopt_token("   ")


def test_exchange_code_surfaces_error_description_instead_of_none(
    tmp_dir: Path,
) -> None:
    """真实失败体没有 msg 字段；旧代码会输出「换取用户 Token失败：None」。"""

    def handler(request) -> object:
        if "app_access_token" in str(request.url):
            return json_response({"code": 0, "app_access_token": "app-token"})
        return json_response(
            {
                "code": 20003,
                "error": "invalid_grant",
                "error_description": "授权码已过期，请重新授权",
            }
        )

    manager = _manager(tmp_dir, handler)

    with pytest.raises(CollectorError) as excinfo:
        manager.exchange_code("stale-code")

    message = str(excinfo.value)
    assert "授权码已过期，请重新授权" in message
    assert "code=20003" in message
    assert "None" not in message


def test_exchange_code_keeps_the_access_token_and_refresh_token(
    tmp_dir: Path,
) -> None:
    def handler(request) -> object:
        if "app_access_token" in str(request.url):
            return json_response({"code": 0, "app_access_token": "app-token"})
        return json_response(
            {
                "code": 0,
                "data": {
                    "access_token": "u-from-code",
                    "refresh_token": "r-from-code",
                    "expires_in": 7200,
                },
            }
        )

    manager = _manager(tmp_dir, handler, clock=lambda: 500.0)

    manager.exchange_code("good-code")

    payload = json.loads((tmp_dir / "lark_user_token.json").read_text(encoding="utf-8"))
    assert payload["access_token"] == "u-from-code"
    assert payload["refresh_token"] == "r-from-code"
    assert payload["expires_at"] == pytest.approx(7700.0)