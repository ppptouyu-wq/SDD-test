"""Task 7 验收标准：邮件推送模块单元测试。

- send() 函数签名符合 design.md §4.3 的接口定义
- 邮件主题包含日期和团队名称
- 邮件正文为 HTML 格式的日报内容
- 发送失败时重试2次，仍失败则返回 False+错误日志
- 使用 Mock SMTP 的单元测试通过
"""

from __future__ import annotations

import smtplib
from datetime import date, datetime

from notifier import email as email_notifier
from shared.models import DailyReport, MemberReport

DAY = date(2026, 8, 20)


def _report() -> DailyReport:
    return DailyReport(
        date=DAY,
        team_name="平台研发组",
        members=[MemberReport(name="张三", github_username="zhangsan")],
        generated_at=datetime(2026, 8, 20, 18, 0),
        markdown="# 平台研发组工作日报 — 2026-08-20\n\n### 代码提交\n- 今日无记录\n",
        html="<!DOCTYPE html><html><body><h1>平台研发组工作日报</h1></body></html>",
    )


class FakeSMTP:
    """记录调用并可被设定为失败的假 SMTP 服务端。"""

    instances: list["FakeSMTP"] = []

    def __init__(self, host: str, port: int, *, fail_times: int = 0) -> None:
        self.host = host
        self.port = port
        self.sent: list[object] = []
        self.logged_in: tuple[str, str] | None = None
        self.entered = False
        self.fail_times = fail_times
        self.attempt = 0
        FakeSMTP.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        self.entered = True
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.entered = False

    def login(self, user: str, password: str) -> None:
        self.logged_in = (user, password)

    def send_message(self, message) -> None:  # noqa: ANN001
        self.attempt += 1
        if self.attempt <= self.fail_times:
            raise smtplib.SMTPConnectError(421, "服务不可用")
        self.sent.append(message)

    # 记录到实例上的失败次数
    def _factory(self):  # pragma: no cover - helper for readability
        return self


def _factory(fail_times: int = 0):
    """构造一个 SMTP 工厂，每次调用返回一个新的 FakeSMTP。"""
    created: list[FakeSMTP] = []

    def factory(host: str, port: int) -> FakeSMTP:
        server = FakeSMTP(host, port, fail_times=fail_times)
        created.append(server)
        return server

    factory.created = created  # type: ignore[attr-defined]
    return factory


def test_send_delivers_html_report(config):
    factory = _factory()
    report = _report()

    ok = email_notifier.send(
        report, ["leader@example.com"], config=config.email, smtp_factory=factory
    )

    assert ok is True
    server = factory.created[0]
    assert server.entered is False  # with 已退出
    assert server.logged_in == ("bot@example.com", "pw")
    assert len(server.sent) == 1


def test_subject_contains_date_and_team_name(config):
    factory = _factory()
    report = _report()

    email_notifier.send(report, ["leader@example.com"], config=config.email, smtp_factory=factory)

    message = factory.created[0].sent[0]
    subject = message["Subject"]
    assert "平台研发组" in subject
    assert "2026-08-20" in subject


def test_body_is_html(config):
    factory = _factory()
    report = _report()

    email_notifier.send(report, ["leader@example.com"], config=config.email, smtp_factory=factory)

    message = factory.created[0].sent[0]
    html_parts = [p for p in message.walk() if p.get_content_type() == "text/html"]
    assert html_parts, "邮件必须包含 text/html 部分"
    assert "<h1>平台研发组工作日报</h1>" in html_parts[0].get_content()


def test_failure_retries_twice_then_returns_false(config):
    """发送失败时重试2次，仍失败则返回 False + 错误日志。"""
    factory = _factory(fail_times=99)
    report = _report()

    ok = email_notifier.send(
        report, ["leader@example.com"], config=config.email, smtp_factory=factory
    )

    assert ok is False
    assert len(factory.created) == 3  # 首次 + 2 次重试
    assert config.email.max_retries == 2


def test_recovers_on_second_attempt(config):
    """第一次失败、第二次成功时整体应返回 True。"""
    created: list[FakeSMTP] = []
    state = {"attempt": 0}

    def factory(host: str, port: int) -> FakeSMTP:
        server = FakeSMTP(host, port)
        created.append(server)
        return server

    class FailingOnce(FakeSMTP):
        def send_message(self, message) -> None:  # noqa: ANN001
            state["attempt"] += 1
            if state["attempt"] == 1:
                raise smtplib.SMTPServerDisconnected("连接中断")
            self.sent.append(message)

    def factory2(host: str, port: int) -> FakeSMTP:
        server = FailingOnce(host, port)
        created.append(server)
        return server

    ok = email_notifier.send(
        _report(), ["leader@example.com"], config=config.email, smtp_factory=factory2
    )

    assert ok is True
    assert len(created) == 2


def test_missing_recipients_returns_false(config):
    ok = email_notifier.send(_report(), [], config=config.email, smtp_factory=_factory())

    assert ok is False


def test_missing_smtp_host_returns_false(config):
    from dataclasses import replace

    ok = email_notifier.send(
        _report(),
        ["leader@example.com"],
        config=replace(config.email, smtp_host=""),
        smtp_factory=_factory(),
    )

    assert ok is False
