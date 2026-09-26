"""第 7.4.3 节四步法 —— 第三步补测试（2/2）：修正版契约测试。

依据 `specs/contracts/legacy-baseline.md` 定稿契约逐条验证。
每条测试都标注它对应契约里的哪一项判定。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from legacy_fixed.collector import github as fixed_github  # noqa: E402
from legacy_fixed.collector.github import (  # noqa: E402
    AuthError,
    CollectorError,
    GithubCollector,
)
from legacy_fixed.report import builder as fixed_report  # noqa: E402
from legacy_fixed.report.builder import NotifyError, build, send  # noqa: E402


# ============================================================ 测试替身

class FakeResponse:
    def __init__(self, code=200, body=None, headers=None):
        import json as _json

        self._code = code
        self._body = _json.dumps(body if body is not None else []).encode("utf-8")
        self.headers = headers or {}

    def getcode(self):
        return self._code

    def read(self):
        return self._body


def make_opener(responses):
    """按顺序返回预设响应；用完后重复最后一个。"""
    state = {"i": 0}

    def opener(request, timeout):
        idx = min(state["i"], len(responses) - 1)
        state["i"] += 1
        item = responses[idx]
        if isinstance(item, Exception):
            raise item
        return item

    opener.calls = state
    return opener


COMMIT = {
    "sha": "abc123",
    "commit": {"message": "feat: 采集", "author": {"name": "张三", "date": "2026-04-24T12:00:00Z"}},
}
DETAIL = {"stats": {"additions": 7, "deletions": 3}, "files": [{"filename": "a.py"}]}


# ================================================ collector 契约
#
# 注意：分页循环遇到"非空列表"会继续翻下一页，最后由空列表结束。
# 因此每个用例的响应序列都要以 FakeResponse(200, []) 收尾，
# 否则替身会一直重复最后一个响应，导致无限翻页。

EMPTY_PAGE = FakeResponse(200, [])


def test_collect_returns_structured_records_not_text():
    """契约 1.2-#5：采集层只返回结构化记录，不再拼文本。"""
    opener = make_opener([FakeResponse(200, [COMMIT]), FakeResponse(200, DETAIL), EMPTY_PAGE])
    collector = GithubCollector(["o/r"], "t", sleep=lambda s: None, opener=opener)

    records = collector.collect("2026-04-01", "2026-05-01")

    assert isinstance(records, list)
    assert not isinstance(records[0], str)
    assert records[0].author == "张三"
    assert records[0].repo == "o/r"


def test_missing_stats_are_none_not_zero():
    """契约 1.2-#6：取不到统计字段时为 None（未知），绝不是 0。

    请求顺序是：①提交列表页 → ②该条 commit 详情（此处失败）→ ③下一页（空，结束）。
    """
    opener = make_opener(
        [FakeResponse(200, [COMMIT]), FakeResponse(500, {}), EMPTY_PAGE]
    )
    collector = GithubCollector(["o/r"], "t", sleep=lambda s: None, opener=opener)

    records = collector.collect("a", "b")

    assert len(records) == 1
    assert records[0].additions is None
    assert records[0].deletions is None
    assert records[0].files_changed is None


def test_empty_repo_list_returns_empty():
    """契约 1.3：仓库列表为空 -> 返回空列表，不发起请求。"""
    opener = make_opener([FakeResponse(200, [])])
    collector = GithubCollector([], "t", sleep=lambda s: None, opener=opener)

    assert collector.collect("a", "b") == []
    assert opener.calls["i"] == 0


def test_auth_failure_raises_and_does_not_retry():
    """契约 1.3：401 立即抛 AuthError，不重试。"""
    opener = make_opener([FakeResponse(401, {})])
    collector = GithubCollector(["o/r"], "bad", sleep=lambda s: None, opener=opener)

    with pytest.raises(AuthError):
        collector.collect("a", "b")
    assert opener.calls["i"] == 1  # 只请求一次


def test_one_repo_failure_does_not_block_others():
    """契约 1.3：单仓库失败不阻断其他仓库，但记录失败仓库名。"""

    def opener(request, timeout):
        if "bad/repo" in request.full_url:
            return FakeResponse(500, {})
        if "/commits/" in request.full_url:
            return FakeResponse(200, DETAIL)
        if "page=1" in request.full_url:
            return FakeResponse(200, [COMMIT])
        return EMPTY_PAGE

    collector = GithubCollector(
        ["bad/repo", "o/r"], "t", sleep=lambda s: None, opener=opener
    )
    records = collector.collect("a", "b")

    assert collector.failed_repos == ["bad/repo"]
    assert len(records) == 1 and records[0].repo == "o/r"


def test_rate_limit_wait_is_capped():
    """契约 1.2-#3：限流等待按 X-RateLimit-Reset 计算，且有上限。"""
    import time as _time

    waits: list[float] = []
    far_future = int(_time.time()) + 99999  # 远超上限

    responses = [
        FakeResponse(403, {}, {"X-RateLimit-Reset": str(far_future)}),
        EMPTY_PAGE,
    ]
    opener = make_opener(responses)
    collector = GithubCollector(["o/r"], "t", sleep=waits.append, opener=opener)

    collector.collect("a", "b")

    assert waits, "应发生等待"
    assert waits[0] == fixed_github.MAX_RATE_LIMIT_WAIT, "等待必须被上限截断"


def test_retries_then_succeeds():
    """契约 1.2-#1：重试 3 次、间隔 5s。"""
    waits: list[float] = []
    responses = [
        ConnectionError("网络抖动"),
        EMPTY_PAGE,
    ]
    opener = make_opener(responses)
    collector = GithubCollector(["o/r"], "t", sleep=waits.append, opener=opener)

    collector.collect("a", "b")

    assert waits == [fixed_github.RETRY_INTERVAL]


def test_retries_exhausted_marks_repo_failed_without_raising():
    """契约 1.3：单仓库重试耗尽 -> 记录失败仓库名，但**不阻断**整体流程。

    注意这里不是抛异常：契约明确要求"单仓库失败不阻断其他仓库"，
    因此 collect() 应正常返回空列表并把仓库记入 failed_repos。
    """
    opener = make_opener([ConnectionError("一直失败")])
    collector = GithubCollector(["o/r"], "t", sleep=lambda s: None, opener=opener)

    records = collector.collect("a", "b")

    assert records == []
    assert collector.failed_repos == ["o/r"]


def test_single_repo_failure_raises_when_asked_directly():
    """底层 _collect_repo 在重试耗尽后应抛 CollectorError（供上层决定如何处理）。"""
    opener = make_opener([ConnectionError("一直失败")])
    collector = GithubCollector(["o/r"], "t", sleep=lambda s: None, opener=opener)

    with pytest.raises(CollectorError):
        collector._collect_repo("o/r", "a", "b")


@pytest.mark.parametrize(
    "code, expected",
    [(200, True), (401, False), (403, False), (500, False)],
)
def test_check_token_only_passes_on_200(code, expected):
    """契约 1.2-#7：只有 200 才算通过，不再掩盖失败。"""
    opener = make_opener([FakeResponse(code, {})])
    collector = GithubCollector(["o/r"], "t", sleep=lambda s: None, opener=opener)

    assert collector.check_token() is expected


def test_constants_are_named_not_magic():
    """契约 1.2-#1/#2/#4：魔法数字必须成为具名常量。"""
    assert fixed_github.MAX_RETRIES == 3
    assert fixed_github.RETRY_INTERVAL == 5.0
    assert fixed_github.TIMEOUT == 30.0
    assert fixed_github.PER_PAGE == 30


# ================================================ report 契约

def test_report_format_is_declared_contract():
    """契约 2.4：文本格式一旦定稿即为对外契约。"""
    result = build(
        [{"who": "张三", "msg": "写功能", "add": 10, "del": 2}], "平台组"
    )

    assert result.text.startswith("【平台组】今日工作")
    assert "== 张三 ==" in result.text
    assert "- 写功能 (+10/-2)" in result.text


def test_report_sorts_members_explicitly():
    """契约 2.2-#1：排序是显式声明的契约行为。"""
    result = build(
        [
            {"who": "王五", "msg": "b", "add": 0, "del": 0},
            {"who": "张三", "msg": "a", "add": 0, "del": 0},
        ],
        "组",
    )

    assert result.text.index("== 张三 ==") < result.text.index("== 王五 ==")


def test_report_truncates_with_named_constant():
    """契约 2.2-#2：MESSAGE_MAX_LEN 为具名常量。"""
    long_msg = "x" * (fixed_report.MESSAGE_MAX_LEN + 50)
    result = build([{"who": "张三", "msg": long_msg, "add": 0, "del": 0}], "组")

    line = [ln for ln in result.text.splitlines() if ln.startswith("- ")][0]
    body = line[len("- ") : line.rindex(" (+")]
    assert body == "x" * fixed_report.MESSAGE_MAX_LEN + "..."


def test_report_caps_items_with_named_constant():
    """契约 2.2-#3：MAX_ITEMS_PER_MEMBER 为具名常量。"""
    many = [
        {"who": "张三", "msg": f"m{i}", "add": 0, "del": 0}
        for i in range(fixed_report.MAX_ITEMS_PER_MEMBER + 30)
    ]
    result = build(many, "组")

    assert result.counts["张三"] == fixed_report.MAX_ITEMS_PER_MEMBER
    assert result.text.count("\n- ") == fixed_report.MAX_ITEMS_PER_MEMBER


def test_report_empty_records_returns_header_only():
    """契约 2.3：records 为空 -> 仅含标题，不抛异常。"""
    result = build([], "空组")
    assert result.text.strip() == "【空组】今日工作"
    assert result.counts == {}


def test_report_decoupled_from_smtp_credentials():
    """契约 2.2-#4：生成不再依赖 SMTP 凭据（职责分离）。"""
    import inspect

    signature = inspect.signature(build)
    assert "smtp_host" not in signature.parameters
    assert "smtp_password" not in signature.parameters


# ---------------------------------------------------------- 发送

class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, timeout=None, *, fail=False):
        self.host, self.port, self.timeout = host, port, timeout
        self.sent = []
        self.logged_in = None
        self.fail = fail
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, message):
        if self.fail:
            raise RuntimeError("SMTP 拒绝")
        self.sent.append(message)


def test_send_uses_configurable_port_and_timeout():
    """契约 2.2-#5：端口与超时可配，不再硬编码 465。"""
    FakeSMTP.instances.clear()
    send(
        "正文", ["a@x.com"],
        smtp_host="h", smtp_user="u", smtp_password="p",
        port=2525, timeout=9.0, smtp_factory=FakeSMTP,
    )

    server = FakeSMTP.instances[0]
    assert server.port == 2525
    assert server.timeout == 9.0


def test_send_wraps_failure_in_notify_error():
    """契约 2.2-#5：异常统一包装为 NotifyError，不外泄 smtplib/底层异常。"""
    def broken_factory(host, port, timeout=None):
        return FakeSMTP(host, port, timeout, fail=True)

    with pytest.raises(NotifyError) as excinfo:
        send(
            "正文", ["a@x.com"],
            smtp_host="h", smtp_user="u", smtp_password="p",
            smtp_factory=broken_factory,
        )
    assert "SMTP 拒绝" in str(excinfo.value)


def test_send_rejects_empty_recipients():
    with pytest.raises(NotifyError):
        send("x", [], smtp_host="h", smtp_user="u", smtp_password="p")


def test_send_rejects_missing_host():
    with pytest.raises(NotifyError):
        send("x", ["a@x.com"], smtp_host="", smtp_user="u", smtp_password="p")


def test_send_success_returns_true():
    FakeSMTP.instances.clear()
    assert send(
        "正文", ["a@x.com"], smtp_host="h", smtp_user="u", smtp_password="p",
        smtp_factory=FakeSMTP,
    ) is True
    assert FakeSMTP.instances[0].logged_in == ("u", "p")
