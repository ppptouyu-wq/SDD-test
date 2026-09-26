"""多工具规范配置生成器（第 3 章 3.2 节）。

书中 3.2 讲"AI 辅助编程工具与 SDD 的结合"，列举了四类工具的规范加载方式：

| 工具 | 加载方式 | 书中小节 |
|---|---|---|
| Claude Code | `CLAUDE.md` + Skills + Hooks | 3.2.1 |
| Cursor | `.cursor/rules/*.mdc` 或 `.cursorrules` | 3.2.2 |
| GitHub Copilot | `.github/copilot-instructions.md` | 3.2.3 |
| Kiro | `.kiro/steering/*.md` + Specs | 3.2.4 |

**为什么要用生成器而不是手写四份**：同一套规则写成四份，必然会漂移 ——
改了 `CLAUDE.md` 忘了改 `.cursorrules`，团队里用不同工具的人就会遵守不同规范。
这与主项目里"接口契约必须集中定义"是同一个道理。

唯一事实来源：`CLAUDE.md`。其余三份由本脚本生成并提交，
`tests/test_tool_configs.py` 会校验它们与 `CLAUDE.md` 保持同步。

用法：
    python scripts/generate_tool_configs.py          # 生成/更新
    python scripts/generate_tool_configs.py --check  # 只校验是否同步（CI 用）
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MD = ROOT / "CLAUDE.md"

CURSOR_RULES = ROOT / ".cursor" / "rules" / "sdd.mdc"
CURSOR_LEGACY = ROOT / ".cursorrules"
COPILOT_INSTRUCTIONS = ROOT / ".github" / "copilot-instructions.md"
KIRO_STEERING = ROOT / ".kiro" / "steering" / "project.md"

GENERATED_MARKER = "<!-- 由 scripts/generate_tool_configs.py 生成；请勿手工编辑 -->"

# 从 CLAUDE.md 抽取的核心小节（这些是"项目规则"正文）
EXTRACTED_SECTIONS = ("项目规范", "开发规则", "目录结构")


def _force_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover
                pass


def read_source() -> str:
    if not CLAUDE_MD.exists():
        raise FileNotFoundError(f"唯一事实来源不存在：{CLAUDE_MD}")
    return CLAUDE_MD.read_text(encoding="utf-8")


def extract_sections(text: str) -> str:
    """抽取 CLAUDE.md 中"项目规范 / 开发规则 / 目录结构"三节正文。"""
    parts: list[str] = []
    for name in EXTRACTED_SECTIONS:
        pattern = re.compile(
            rf"^##\s+{re.escape(name)}\s*$\n(.*?)(?=^##\s|\Z)",
            re.MULTILINE | re.DOTALL,
        )
        match = pattern.search(text)
        if match:
            parts.append(f"## {name}\n{match.group(1).rstrip()}\n")
    if not parts:
        raise ValueError("未能从 CLAUDE.md 抽取到任何约定小节，请检查标题是否被改动")
    return "\n".join(parts)


def render_cursor(body: str) -> str:
    """Cursor 规则文件（.mdc 带 frontmatter，供 Cursor 识别生效范围）。"""
    return (
        "---\n"
        "description: SDD 规范驱动开发项目规则（智能日报生成器）\n"
        "globs: ['**/*.py', 'specs/**/*.md']\n"
        "alwaysApply: true\n"
        "---\n"
        f"{GENERATED_MARKER}\n\n"
        "# 智能日报生成器 — Cursor 规则\n\n"
        "> 与 `CLAUDE.md` 同源。改规范请改 `CLAUDE.md` 后重新生成本文件。\n\n"
        f"{body}"
    )


def render_cursorlegacy(body: str) -> str:
    """旧版 Cursor 用 .cursorrules（纯文本，无 frontmatter）。"""
    return (
        f"{GENERATED_MARKER}\n"
        "# 智能日报生成器 — Cursor 规则（旧版 .cursorrules）\n"
        "# 与 CLAUDE.md 同源。\n\n"
        f"{body}"
    )


def render_copilot(body: str) -> str:
    """GitHub Copilot 的自定义指令文件。"""
    return (
        f"{GENERATED_MARKER}\n"
        "# Copilot 自定义指令 — 智能日报生成器\n\n"
        "> 与 `CLAUDE.md` 同源。Copilot 会在本仓库内自动加载本文件。\n\n"
        f"{body}"
    )


def render_kiro(body: str) -> str:
    """Kiro 的 steering 文件（书 3.2.4：把 Spec 流程内置进 IDE）。"""
    return (
        "---\n"
        "inclusion: always\n"
        "---\n"
        f"{GENERATED_MARKER}\n\n"
        "# 项目 Steering — 智能日报生成器\n\n"
        "> 与 `CLAUDE.md` 同源。Kiro 的 Specs 工作流与本项目的三份规范一一对应：\n"
        "> `requirements.md` ↔ `specs/proposal.md`、`design.md` ↔ `specs/design.md`、\n"
        "> `tasks.md` ↔ `specs/tasks.md`。\n\n"
        f"{body}"
    )


TARGETS: list[tuple[Path, str]] = [
    (CURSOR_RULES, "cursor"),
    (CURSOR_LEGACY, "cursorrules"),
    (COPILOT_INSTRUCTIONS, "copilot"),
    (KIRO_STEERING, "kiro"),
]

_RENDERERS = {
    "cursor": render_cursor,
    "cursorrules": render_cursorlegacy,
    "copilot": render_copilot,
    "kiro": render_kiro,
}


def generate() -> dict[Path, str]:
    """计算所有目标文件应当具有的内容。"""
    body = extract_sections(read_source())
    return {path: _RENDERERS[kind](body) for path, kind in TARGETS}


def write_all(dry_run: bool = False) -> list[Path]:
    written: list[Path] = []
    for path, content in generate().items():
        if path.exists() and path.read_text(encoding="utf-8") == content:
            continue
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def check_sync() -> list[Path]:
    """返回与 CLAUDE.md 不同步的文件（CI 用）。"""
    stale: list[Path] = []
    for path, content in generate().items():
        if not path.exists():
            stale.append(path)
            continue
        if path.read_text(encoding="utf-8") != content:
            stale.append(path)
    return stale


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    parser = argparse.ArgumentParser(description="生成多工具规范配置")
    parser.add_argument("--check", action="store_true", help="只校验同步状态，不写入")
    args = parser.parse_args(argv)

    if args.check:
        stale = check_sync()
        if stale:
            print("以下文件与 CLAUDE.md 不同步：")
            for path in stale:
                print(f"  - {path.relative_to(ROOT)}")
            print("\n请运行：python scripts/generate_tool_configs.py")
            return 1
        print("✓ 四个工具配置与 CLAUDE.md 保持同步")
        return 0

    written = write_all()
    if written:
        print("已更新：")
        for path in written:
            print(f"  - {path.relative_to(ROOT)}")
    else:
        print("✓ 所有工具配置已是最新，无需改动")
    return 0


if __name__ == "__main__":
    sys.exit(main())
