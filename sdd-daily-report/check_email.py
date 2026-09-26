"""发信前自检：确认配置、邮件构造、SMTP 连通性。

不打印任何凭据内容，只报告"是否就绪"。
用法：python check_email.py
"""
import io
import smtplib
import socket
import sys
from datetime import date, datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, ".")

from notifier.email import build_message, send  # noqa: E402
from shared.config import load_config  # noqa: E402
from shared.models import DailyReport, MemberReport  # noqa: E402

cfg = load_config("config.github.yaml")
print("=" * 62)
print("一、配置读取结果")
print("=" * 62)
print(f"  smtp_host        : {cfg.email.smtp_host}")
print(f"  smtp_port        : {cfg.email.smtp_port}")
print(f"  smtp_user        : {cfg.email.smtp_user!r}")
print(f"  sender           : {cfg.email.sender!r}")
print(f"  recipients       : {cfg.email.recipients}")
print(f"  use_ssl          : {cfg.email.use_ssl}")
print(f"  SMTP_PASSWORD    : {'已设置（长度 %d）' % len(cfg.email.smtp_password) if cfg.email.smtp_password else '未设置'}")

print()
print("=" * 62)
print("二、邮件构造（不发送）")
print("=" * 62)
report = DailyReport(
    date=date(2026, 4, 24),
    team_name="个人测试仓库",
    members=[MemberReport(name="示例成员", github_username="alice")],
    generated_at=datetime.now(),
    markdown="# 示例团队工作日报 — 2026-04-24\n\n### 代码提交\n- `example-org/example-repo` 首次提交（+3351/-0，32 个文件）\n",
    html="<!DOCTYPE html><html><body><h1>个人测试仓库工作日报</h1></body></html>",
)
msg = build_message(report, cfg.email.recipients, cfg.email.sender)
print(f"  Subject : {msg['Subject']}")
print(f"  From    : {msg['From']}")
print(f"  To      : {msg['To']}")
parts = [p.get_content_type() for p in msg.walk()]
print(f"  正文分段 : {parts}")
print(f"  → 主题含日期与团队名：{'2026-04-24' in msg['Subject'] and '个人测试仓库' in msg['Subject']}")

print()
print("=" * 62)
print("三、SMTP 连通性（仅握手，不登录、不发信）")
print("=" * 62)
host, port = cfg.email.smtp_host, cfg.email.smtp_port
try:
    socket.create_connection((host, port), timeout=10).close()
    print(f"  ✓ 能连通 {host}:{port}")
    with smtplib.SMTP_SSL(host, port, timeout=15) as s:
        code, banner = s.ehlo()
        print(f"  ✓ 服务器响应 {code}: {banner.decode(errors='replace')[:60]}")
except Exception as exc:
    print(f"  ✗ 连接失败：{type(exc).__name__}: {exc}")

print()
print("=" * 62)
print("四、能否真正发信")
print("=" * 62)
missing = []
if not cfg.email.smtp_user:
    missing.append("config 里的 smtp_user")
if not cfg.email.smtp_password:
    missing.append("环境变量 SMTP_PASSWORD（QQ 邮箱 SMTP 授权码）")
if not cfg.email.recipients:
    missing.append("收件人")
if missing:
    print("  还不能发。缺：")
    for item in missing:
        print(f"    - {item}")
    print()
    print("  补齐后运行：python main.py --config config.github.yaml --date 2026-04-24")
else:
    print("  配置齐备，可执行真实发送。")
