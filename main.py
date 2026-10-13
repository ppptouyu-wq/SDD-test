"""Harness 入口 —— 会话状态、异步 workflow、源码编辑与回滚的外壳驱动。

用法：
    python main.py session_start --mode autonomous
    python main.py handle_message "<消息原文>"
    python main.py handle_message --file message.txt
    python main.py check                       # 只跑 shell 自检

设计要点：
- 本入口只做参数解析与 JSON 输出，动作实现全在 `plugins/harness_plugin.py`；
- 输出统一为 JSON（`ensure_ascii=False`，中文直出），便于上游 harness 解析；
- 失败时退出码非零，但仍打印 JSON（便于定位）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins import harness_plugin  # noqa: E402

PLUGIN = harness_plugin.get_plugin()


def _dump(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_session_start(args: argparse.Namespace) -> int:
    payload = PLUGIN.session_start(args.mode)
    _dump(payload)
    return 0 if payload.get("ok") else 1


def cmd_handle_message(args: argparse.Namespace) -> int:
    content = args.content
    if args.file:
        content = Path(args.file).read_text(encoding="utf-8")
    if not content and not sys.stdin.isatty():
        # 从管道读：`echo '<json>' | python main.py handle_message`
        content = sys.stdin.read().strip()
    if not content:
        _dump({"ok": False, "error": "empty message"})
        return 2
    payload = PLUGIN.handle_message(args.chat_id, args.msg_id, content, use_workflow=not args.no_workflow)
    _dump(payload)
    return 0 if payload.get("ok") else 1


def cmd_check(args: argparse.Namespace) -> int:
    message = PLUGIN.state.shell_check_action(args.shell)
    _dump({"ok": "syntax ok" in message, "action": "check", "message": message})
    return 0 if "syntax ok" in message else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="harness（会话 / workflow / 源码编辑 / 回滚）")
    sub = parser.add_subparsers(dest="command")

    start = sub.add_parser("session_start", aliases=["session-start"], help="开始一个会话")
    start.add_argument("--mode", default="autonomous", help="会话模式（默认 autonomous）")
    start.set_defaults(func=cmd_session_start)

    handle = sub.add_parser("handle_message", aliases=["handle-message"], help="处理一条消息")
    handle.add_argument("content", nargs="?", default="", help="消息原文（JSON 文本）")
    handle.add_argument("--file", default="", help="从文件读取消息原文")
    handle.add_argument("--chat-id", default="cli", help="会话 ID")
    handle.add_argument("--msg-id", default="msg-1", help="消息 ID")
    handle.add_argument("--no-workflow", action="store_true", help="同步执行 workflow（测试用）")
    handle.set_defaults(func=cmd_handle_message)

    check = sub.add_parser("check", help="运行 plugins/harness.shell 自检")
    check.add_argument("--shell", default="plugins/harness.shell", help="shell 路径")
    check.set_defaults(func=cmd_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
