"""飞书 Token 管理：应用身份 与 用户身份。

两种身份用途不同，不能互相替代：

| 身份 | Token | 能做什么 | 不能做什么 |
|---|---|---|---|
| 应用身份 | `tenant_access_token` | 发消息、读任务、读考勤、读群信息、读通讯录 | **读群聊消息历史** |
| 用户身份 | `user_access_token` | 读群聊消息历史（`im:message.group_msg` 只支持用户身份） | 需用户手动授权一次 |

这个差异是实测发现的：截图显示 `im:message.group_msg` 的**权限类型是「用户身份」**，
而项目原本全部使用 `tenant_access_token`。因此调用群消息接口恒返回
`230027 need scope: im:message.group_msg` —— 这与版本发布无关，换多少版本都无效。

文件内容：
  - `LarkTokenManager` —— 应用身份（原有）
  - `UserTokenManager`  —— 用户身份（新增，支持授权码换取与自动刷新）
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from shared.errors import CollectorError
from shared.logger import get_logger

logger = get_logger("collector.lark_auth")

# 用户身份读取群聊消息所需的 scope
GROUP_MSG_SCOPE = "im:message.group_msg"

DEFAULT_TOKEN_FILE = Path("data/lark_user_token.json")


@dataclass
class _Token:
    value: str
    expires_at: float


class LarkTokenManager:
    """应用身份 tenant_access_token 的获取与缓存。

    飞书 tenant_access_token 有效期 2 小时；本类在过期前 5 分钟自动刷新。
    当 API 返回 token 失效错误码时，调用 `invalidate()` 强制刷新。
    """

    REFRESH_MARGIN = 300.0  # 提前 5 分钟刷新

    def __init__(
        self,
        client: httpx.Client,
        app_id: str | None,
        app_secret: str | None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client = client
        self._app_id = app_id
        self._app_secret = app_secret
        self._clock = clock
        self._token: _Token | None = None
        self.refresh_count = 0

    def invalidate(self) -> None:
        """标记当前 Token 失效（API 返回过期错误码时调用）。"""
        self._token = None

    def get_token(self) -> str:
        now = self._clock()
        if self._token is not None and now < self._token.expires_at - self.REFRESH_MARGIN:
            return self._token.value
        return self._refresh(now)

    def _refresh(self, now: float) -> str:
        if not self._app_id or not self._app_secret:
            raise CollectorError(
                "飞书凭据缺失：请设置环境变量 LARK_APP_ID / LARK_APP_SECRET",
                source="lark:auth",
            )
        response = self._client.post(
            "/auth/v3/tenant_access_token/internal",
            json={"app_id": self._app_id, "app_secret": self._app_secret},
        )
        if response.status_code >= 400:
            raise CollectorError(
                f"获取飞书 Token 失败：HTTP {response.status_code}",
                source="lark:auth",
            )
        data = response.json()
        if data.get("code") not in (0, None):
            raise CollectorError(
                f"获取飞书 Token 失败：{data.get('msg')}", source="lark:auth"
            )
        token = str(data.get("tenant_access_token", ""))
        expire = float(data.get("expire", 7200))
        self._token = _Token(value=token, expires_at=now + expire)
        self.refresh_count += 1
        logger.info("飞书 Token 已刷新", extra={"source": "lark:auth", "expire": expire})
        return token


class UserTokenManager:
    """用户身份 user_access_token 的授权、持久化与刷新。

    为什么需要它：`im:message.group_msg`（读群聊消息）**只支持用户身份**。
    应用身份即使开通了该权限也拿不到 —— 这是权限类型决定的，不是配置问题。

    使用流程（一次性）：
      1. `authorization_url()` 生成授权链接，用户在浏览器打开并同意
      2. 从回调地址里取出 `code`，调用 `exchange_code(code)` 换取 token
      3. token 落盘（默认 `data/lark_user_token.json`），之后自动读取与刷新

    token 有效期 2 小时，refresh_token 有效期 30 天；本类在过期前自动用
    refresh_token 续期，因此只要 30 天内用过一次就不需要重新授权。
    """

    REFRESH_MARGIN = 300.0

    def __init__(
        self,
        client: httpx.Client,
        app_id: str | None,
        app_secret: str | None,
        *,
        token_file: str | Path = DEFAULT_TOKEN_FILE,
        redirect_uri: str = "https://example.com/callback",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client = client
        self._app_id = app_id
        self._app_secret = app_secret
        self._token_file = Path(token_file)
        self._redirect_uri = redirect_uri
        self._clock = clock
        self._token: _Token | None = None
        self._refresh_token: str | None = None
        self.refresh_count = 0

    # ---------------------------------------------------------- 授权
    def authorization_url(self, *, state: str = "sdd") -> str:
        """生成用户授权链接（用户在浏览器打开并同意授权）。"""
        if not self._app_id:
            raise CollectorError("缺少 app_id，无法生成授权链接", source="lark:auth")
        from urllib.parse import urlencode

        params = urlencode(
            {
                "app_id": self._app_id,
                "redirect_uri": self._redirect_uri,
                "scope": GROUP_MSG_SCOPE,
                "state": state,
            }
        )
        return f"https://open.feishu.cn/open-apis/authen/v1/authorize?{params}"

    def exchange_code(self, code: str) -> None:
        """用授权码换取 user_access_token 并落盘。"""
        if not (self._app_id and self._app_secret):
            raise CollectorError(
                "缺少 app_id / app_secret，无法换取用户 Token", source="lark:auth"
            )
        # 换取用户 token 需要先用 app 身份拿 app_access_token
        app_token = self._app_access_token()
        response = self._client.post(
            "/authen/v1/oidc/access_token",
            json={"grant_type": "authorization_code", "code": code},
            headers={"Authorization": f"Bearer {app_token}"},
        )
        self._handle_token_response(response, action="换取用户 Token")

    def refresh(self) -> None:
        """用 refresh_token 续期。"""
        if not self._refresh_token:
            raise CollectorError(
                "没有 refresh_token，需要重新走用户授权流程", source="lark:auth"
            )
        app_token = self._app_access_token()
        response = self._client.post(
            "/authen/v1/oidc/refresh_access_token",
            json={
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
            },
            headers={"Authorization": f"Bearer {app_token}"},
        )
        self._handle_token_response(response, action="刷新用户 Token")

    def _app_access_token(self) -> str:
        response = self._client.post(
            "/auth/v3/app_access_token/internal",
            json={"app_id": self._app_id, "app_secret": self._app_secret},
        )
        data = response.json()
        if data.get("code") not in (0, None):
            raise CollectorError(
                f"获取 app_access_token 失败：{data.get('msg')}", source="lark:auth"
            )
        return str(data.get("app_access_token", ""))

    def _handle_token_response(self, response: httpx.Response, *, action: str) -> None:
        if response.status_code >= 400:
            raise CollectorError(f"{action}失败：HTTP {response.status_code}", source="lark:auth")
        body = response.json()
        if body.get("code") not in (0, None):
            raise CollectorError(f"{action}失败：{body.get('msg')}", source="lark:auth")
        data = body.get("data") or {}
        token = str(data.get("access_token", ""))
        if not token:
            raise CollectorError(f"{action}失败：响应中没有 access_token", source="lark:auth")
        expire = float(data.get("expires_in", 7200))
        self._token = _Token(value=token, expires_at=self._clock() + expire)
        self._refresh_token = data.get("refresh_token") or self._refresh_token
        self.refresh_count += 1
        self._save()
        logger.info(
            "飞书用户 Token 已更新",
            extra={"source": "lark:auth", "expire": expire, "action": action},
        )

    # -------------------------------------------------------- 持久化
    def _save(self) -> None:
        if self._token is None:
            return
        self._token_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "access_token": self._token.value,
            "expires_at": self._token.expires_at,
            "refresh_token": self._refresh_token,
        }
        self._token_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(
            "用户 Token 已落盘",
            extra={"source": "lark:auth", "file": str(self._token_file)},
        )

    def _load(self) -> bool:
        if not self._token_file.exists():
            return False
        try:
            payload = json.loads(self._token_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        token = payload.get("access_token")
        if not token:
            return False
        self._token = _Token(
            value=str(token), expires_at=float(payload.get("expires_at", 0))
        )
        self._refresh_token = payload.get("refresh_token")
        return True

    # ------------------------------------------------------------ 取值
    def get_token(self) -> str:
        """取得可用的 user_access_token；过期则用 refresh_token 续期。"""
        now = self._clock()
        if self._token is None:
            self._load()
        if self._token is not None and now < self._token.expires_at - self.REFRESH_MARGIN:
            return self._token.value
        if self._refresh_token is None:
            self._load()
        self.refresh()
        assert self._token is not None
        return self._token.value

    def invalidate(self) -> None:
        self._token = None
