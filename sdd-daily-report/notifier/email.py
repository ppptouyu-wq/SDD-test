"""邮件推送模块。

契约来源：specs/design.md §4.3 推送层接口契约 / specs/tasks.md Task 7
    email.send(report, recipients) -> bool

验收标准（tasks.md Task 7）：
- [x] send() 函数签名符合 design.md §4.3 的接口定义
- [x] 邮件主题包含日期和团队名称
- [x] 邮件正文为 HTML 格式的日报内容
- [x] 发送失败时重试2次，仍失败则返回 False + 错误日志
- [x] 使用 Mock SMTP 的单元测试通过

职责边界（design.md §2）：不做数据处理；不做日报生成；不做数据采集。
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from shared.config import EmailConfig
from shared.errors import NotifierError
from shared.logger import get_logger
from shared.models import DailyReport

from generator.template import report_title

logger = get_logger("notifier.email")


def build_message(report: DailyReport, recipients: list[str], sender: str) -> EmailMessage:
    """构造 HTML 邮件。主题包含日期和团队名称。"""
    message = EmailMessage()
    message["Subject"] = report_title(report.team_name, report.date)
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content("本邮件为 HTML 格式，请使用支持 HTML 的邮件客户端查看。")
    message.add_alternative(report.html, subtype="html")
    return message


def _deliver(message: EmailMessage, config: EmailConfig, smtp_factory) -> None:
    """一次投递尝试。"""
    if config.use_ssl:
        with smtp_factory(config.smtp_host, config.smtp_port) as server:
            if config.smtp_user and config.smtp_password:
                server.login(config.smtp_user, config.smtp_password)
            server.send_message(message)
    else:
        with smtp_factory(config.smtp_host, config.smtp_port) as server:
            server.starttls()
            if config.smtp_user and config.smtp_password:
                server.login(config.smtp_user, config.smtp_password)
            server.send_message(message)


def send(
    report: DailyReport,
    recipients: list[str],
    *,
    config: EmailConfig,
    smtp_factory=smtplib.SMTP_SSL,
) -> bool:
    """通过 SMTP 发送 HTML 日报。失败重试 2 次，仍失败返回 False。

    design.md §6.1："邮件发送失败 | 重试 2 次，若仍失败则记录日志和飞书消息告警"
    """
    if not recipients:
        logger.error("邮件推送跳过：未配置收件人", extra={"source": "email"})
        return False
    if not config.smtp_host:
        logger.error("邮件推送跳过：未配置 smtp_host", extra={"source": "email"})
        return False

    sender = config.sender or config.smtp_user
    message = build_message(report, recipients, sender)

    last_error: Exception | None = None
    for attempt in range(config.max_retries + 1):
        try:
            _deliver(message, config, smtp_factory)
            logger.info(
                "邮件推送成功",
                extra={"source": "email", "recipients": recipients, "attempt": attempt + 1},
            )
            return True
        except (smtplib.SMTPException, OSError) as exc:
            last_error = exc
            logger.error(
                "邮件推送失败",
                extra={
                    "source": "email",
                    "attempt": attempt + 1,
                    "max_retries": config.max_retries,
                    "error": str(exc),
                },
            )

    logger.error(
        "邮件推送最终失败", extra={"source": "email", "error": str(last_error)}
    )
    return False
