"""【修正版】日报文本生成 —— 按 specs/contracts/legacy-baseline.md 定稿契约实现。

与 legacy/report/builder.py 的差异（逐条对应契约 2.2 表格）：

  1. 排序不再隐式：契约明确声明"按成员姓名升序"，并在代码注释中指出这是契约行为
  4. 职责分离：生成与推送拆开；构造函数不再持有 SMTP 凭据
  5. send() 端口与超时可配；异常统一包装为 NotifyError，不外泄 smtplib 异常
  6. 补全类型标注

依赖：仅标准库（与基线一致）
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass, field
from email.mime.text import MIMEText
from typing import Iterable, Mapping, Sequence

# 契约 2.2 约定的常量
MESSAGE_MAX_LEN = 60
MAX_ITEMS_PER_MEMBER = 200
DEFAULT_SMTP_PORT = 465
DEFAULT_TIMEOUT = 20.0

TRUNCATE_SUFFIX = "..."
REPORT_HEADER = "【{team}】今日工作"
MEMBER_HEADER = "== {name} =="
ITEM_TEMPLATE = "- {message} (+{additions}/-{deletions})"


class NotifyError(Exception):
    """推送失败。"""


@dataclass
class BuildResult:
    """生成结果：文本 + 每人的条目数（便于调用方判断"今日无记录"）。"""

    text: str
    counts: dict[str, int] = field(default_factory=dict)


def build(records: Iterable[Mapping[str, object]], team: str) -> BuildResult:
    """按契约 2.4 的格式生成纯文本日报。

    格式契约（破坏性变更需先更新 legacy-baseline.md）：
      - 首行：`【团队名】今日工作`
      - 成员段：`== 姓名 ==`
      - 条目：  `- 信息 (+新增/-删除)`

    边界（契约 2.3）：
      - records 为空 -> 返回仅含标题的文本，不抛异常
      - 消息超长 -> 截断并追加 ...
      - 单成员超上限 -> 截断到上限，不报错
    """
    grouped: dict[str, list[Mapping[str, object]]] = {}
    for record in records:
        who = str(record.get("who", "unknown"))
        grouped.setdefault(who, []).append(record)

    # 契约 2.2 第 1 条：按成员姓名升序（显式保证，不是隐式副作用）
    lines = [REPORT_HEADER.format(team=team)]
    counts: dict[str, int] = {}

    for name in sorted(grouped):
        entries = grouped[name][:MAX_ITEMS_PER_MEMBER]
        counts[name] = len(entries)
        lines.append("")
        lines.append(MEMBER_HEADER.format(name=name))
        for record in entries:
            message = str(record.get("msg", ""))
            if len(message) > MESSAGE_MAX_LEN:
                message = message[:MESSAGE_MAX_LEN] + TRUNCATE_SUFFIX
            lines.append(
                ITEM_TEMPLATE.format(
                    message=message,
                    additions=record.get("add", 0),
                    deletions=record.get("del", 0),
                )
            )
    return BuildResult(text="\n".join(lines) + "\n", counts=counts)


def send(
    text: str,
    to: Sequence[str] | str,
    *,
    smtp_host: str,
    smtp_user: str,
    smtp_password: str,
    subject: str = "日报",
    port: int = DEFAULT_SMTP_PORT,
    timeout: float = DEFAULT_TIMEOUT,
    smtp_factory=smtplib.SMTP_SSL,
) -> bool:
    """发送日报。契约 2.2 第 5 条：失败包装为 NotifyError，返回 bool。"""
    recipients = [to] if isinstance(to, str) else list(to)
    if not recipients:
        raise NotifyError("收件人列表为空")
    if not smtp_host:
        raise NotifyError("未配置 smtp_host")

    message = MIMEText(text, "plain", "utf-8")
    message["Subject"] = subject
    message["From"] = smtp_user
    message["To"] = ", ".join(recipients)

    try:
        with smtp_factory(smtp_host, port, timeout=timeout) as server:
            server.login(smtp_user, smtp_password)
            server.send_message(message)
    except Exception as exc:  # noqa: BLE001 - 统一转领域异常
        raise NotifyError(f"邮件发送失败：{exc}") from exc
    return True
