"""specs/tasks.md Task 2 验收标准：飞书文档对接。

- list_documents() 返回 doc_id 列表
- fetch(doc_id) 返回 Document，正文为纯文本
- 单篇文档读取失败抛 SourceError 且消息含 doc_id
- 未配置凭据时抛出明确错误，不静默返回空
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kb_search.models import Document, SourceError
from kb_search.sources import HttpFeishuDocSource, MockFeishuDocSource

NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


def _doc(doc_id: str) -> Document:
    return Document(
        doc_id=doc_id, title=f"标题 {doc_id}", url=f"https://x/{doc_id}",
        content="正文内容", updated_at=NOW,
    )


# --------------------------------------------------------------- Mock 源

def test_mock_list_documents_returns_ids():
    source = MockFeishuDocSource([_doc("d1"), _doc("d2")])
    assert source.list_documents() == ["d1", "d2"]


def test_mock_fetch_returns_document():
    source = MockFeishuDocSource([_doc("d1")])
    document = source.fetch("d1")

    assert isinstance(document, Document)
    assert document.doc_id == "d1"
    assert document.content == "正文内容"
    assert document.title == "标题 d1"


def test_mock_fetch_missing_raises_with_doc_id():
    source = MockFeishuDocSource([])
    with pytest.raises(SourceError) as excinfo:
        source.fetch("nope")
    assert "nope" in str(excinfo.value)  # 消息必须含 doc_id


def test_mock_fetch_failure_raises_source_error():
    source = MockFeishuDocSource([_doc("d1")], fail_for=["d1"])
    with pytest.raises(SourceError) as excinfo:
        source.fetch("d1")
    assert "d1" in str(excinfo.value)


# --------------------------------------------------------------- HTTP 源

def test_http_source_without_credentials_raises_clearly(monkeypatch):
    """未配置凭据时必须抛错，不能静默返回空列表让同步"假成功"。"""
    monkeypatch.delenv("LARK_APP_ID", raising=False)
    monkeypatch.delenv("LARK_APP_SECRET", raising=False)
    source = HttpFeishuDocSource(app_id=None, app_secret=None)

    with pytest.raises(SourceError) as excinfo:
        source.list_documents()

    assert "LARK_APP_ID" in str(excinfo.value) or "凭据" in str(excinfo.value)


def test_http_source_fetch_wraps_error_with_doc_id(monkeypatch):
    """任何底层错误转成 SourceError 时都必须带上 doc_id（design.md §6.1）。"""
    source = HttpFeishuDocSource(app_id="a", app_secret="s")
    source._token = "t"  # 跳过取 token

    def boom(path, params=None):
        raise SourceError("飞书 API 返回 HTTP 500")

    monkeypatch.setattr(source, "_get", boom)

    with pytest.raises(SourceError) as excinfo:
        source.fetch("doc-xyz")

    assert "doc-xyz" in str(excinfo.value)
    assert "500" in str(excinfo.value)


def test_http_source_parses_document(monkeypatch):
    source = HttpFeishuDocSource(app_id="a", app_secret="s")
    source._token = "t"

    def fake_get(path, params=None):
        if "raw_content" in path:
            return {"code": 0, "data": {"content": "这是正文纯文本"}}
        return {"code": 0, "data": {"document": {"title": "限流方案", "revision_id": 1723000000}}}

    monkeypatch.setattr(source, "_get", fake_get)

    document = source.fetch("doc-1")

    assert document.title == "限流方案"
    assert document.content == "这是正文纯文本"
    assert document.url.endswith("doc-1")
    assert isinstance(document.updated_at, datetime)


def test_http_source_lists_documents(monkeypatch):
    source = HttpFeishuDocSource(app_id="a", app_secret="s")
    source._token = "t"
    monkeypatch.setattr(
        source, "_get",
        lambda path, params=None: {"code": 0, "data": {"files": [{"token": "d1"}, {"token": "d2"}]}},
    )

    assert source.list_documents() == ["d1", "d2"]
