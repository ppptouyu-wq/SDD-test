"""飞书文档接入：接口 + Mock 实现 + HTTP 实现。

契约来源：specs/design.md §4、specs/tasks.md Task 2
    - list_documents() 返回 doc_id 列表
    - fetch(doc_id) 返回 Document，正文为纯文本
    - 飞书 Token 过期时自动刷新后重试1次
    - 单篇文档读取失败抛 SourceError 且消息含 doc_id
    - 未配置凭据时抛出明确错误，不静默返回空
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol, Sequence

from kb_search.models import Document, SourceError

TOKEN_INVALID_CODES = {99991663, 99991664, 99991661, 99991668}


class FeishuDocSource(Protocol):
    """飞书文档源接口。"""

    def list_documents(self) -> list[str]:
        ...

    def fetch(self, doc_id: str) -> Document:
        ...


class MockFeishuDocSource:
    """Mock 飞书文档源：本地运行与测试用。

    支持注入失败（`fail_for`）以验证"单篇失败不阻断其余文档"的降级行为。
    """

    def __init__(
        self,
        documents: Sequence[Document] | None = None,
        *,
        fail_for: Sequence[str] | None = None,
    ) -> None:
        self._documents = {d.doc_id: d for d in (documents or [])}
        self.fail_for = set(fail_for or [])
        self.fetch_calls: list[str] = []

    def list_documents(self) -> list[str]:
        return sorted(self._documents)

    def fetch(self, doc_id: str) -> Document:
        self.fetch_calls.append(doc_id)
        if doc_id in self.fail_for:
            raise SourceError("飞书文档读取失败（模拟）", doc_id=doc_id)
        document = self._documents.get(doc_id)
        if document is None:
            raise SourceError("文档不存在", doc_id=doc_id)
        return document


class HttpFeishuDocSource:
    """真实飞书文档源。

    需要 `LARK_APP_ID` / `LARK_APP_SECRET`。未配置凭据时抛 SourceError，
    而不是返回空列表 —— 静默返回空会让同步任务"成功但什么都没做"。
    """

    def __init__(
        self,
        *,
        app_id: str | None = None,
        app_secret: str | None = None,
        folder_token: str = "",
        api_base: str = "https://open.feishu.cn/open-apis",
        timeout: float = 15.0,
        max_retries: int = 2,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.folder_token = folder_token
        self.api_base = api_base
        self.timeout = timeout
        self.max_retries = max_retries
        self._token: str | None = None

    # ---- Token ----
    def _refresh_token(self) -> str:
        if not self.app_id or not self.app_secret:
            raise SourceError("缺少凭据：请设置环境变量 LARK_APP_ID / LARK_APP_SECRET")
        import httpx

        try:
            response = httpx.post(
                f"{self.api_base}/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise SourceError(f"获取飞书 Token 失败：{exc}") from exc

        if payload.get("code") not in (0, None):
            raise SourceError(f"获取飞书 Token 失败：{payload.get('msg')}")
        self._token = str(payload.get("tenant_access_token", ""))
        return self._token

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        """带"Token 过期刷新后重试 1 次"的 GET。"""
        import httpx

        if self._token is None:
            self._refresh_token()

        token_retried = False
        last_error: Exception | None = None
        for _attempt in range(self.max_retries + 1):
            try:
                response = httpx.get(
                    f"{self.api_base}{path}",
                    params=params or {},
                    headers={"Authorization": f"Bearer {self._token}"},
                    timeout=self.timeout,
                )
                if response.status_code >= 400:
                    raise SourceError(f"飞书 API 返回 HTTP {response.status_code}")
                payload = response.json()

                if payload.get("code") in TOKEN_INVALID_CODES and not token_retried:
                    token_retried = True
                    self._token = None
                    self._refresh_token()
                    continue
                if payload.get("code") not in (0, None):
                    raise SourceError(f"飞书 API 错误：{payload.get('msg')}")
                return payload
            except SourceError as exc:
                last_error = exc
            except Exception as exc:  # noqa: BLE001
                last_error = SourceError(f"飞书 API 调用失败：{exc}")
        raise SourceError(str(last_error or "飞书 API 调用失败"))

    # ---- 接口实现 ----
    def list_documents(self) -> list[str]:
        payload = self._get(
            "/docx/v1/files", {"folder_token": self.folder_token, "page_size": 100}
        )
        files = ((payload.get("data") or {}).get("files")) or []
        return [str(f.get("token")) for f in files if f.get("token")]

    def fetch(self, doc_id: str) -> Document:
        try:
            meta = self._get(f"/docx/v1/documents/{doc_id}")
            raw = self._get(f"/docx/v1/documents/{doc_id}/raw_content")
        except SourceError as exc:
            # 保证消息里一定带 doc_id（design.md §6.1 要求可定位）
            raise SourceError(exc.message, doc_id=doc_id) from exc

        data = meta.get("data") or {}
        document = data.get("document") or {}
        content = str(((raw.get("data") or {}).get("content")) or "")
        revision = document.get("revision_id")
        updated = (
            datetime.fromtimestamp(int(revision), tz=timezone.utc)
            if isinstance(revision, int)
            else datetime.now(tz=timezone.utc)
        )
        return Document(
            doc_id=doc_id,
            title=str(document.get("title") or doc_id),
            url=f"https://feishu.cn/docx/{doc_id}",
            content=content,
            updated_at=updated,
        )
