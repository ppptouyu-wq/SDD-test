"""Shell 自检脚本 —— 检查 plugins/harness.shell 是否可解析、契约完整、且真能跑通。

用法：
    python scripts/check_shell_test.py [plugins/harness.shell]

检查项：
1. 语法：ast.parse 通过（失败即非零退出）；
2. 契约：声明了插件名、workflow 名、action 名与消息字段（action/workflow/check/tasks）；
3. 落点：plugins/harness_plugin.py 存在且可编译；
4. 运行时：以子进程跑一次 `python main.py check`，要求退出码 0 且输出含 "syntax ok"
   （静态检查只能证明"看起来对"，这一步证明"真的能跑"）。

退出码：0 = 全部通过；1 = 有检查项失败。
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHELL = ROOT / "plugins" / "harness.shell"
PLUGIN = ROOT / "plugins" / "harness_plugin.py"
ENTRY = ROOT / "main.py"

PLUGIN_NEEDLES = ("source_edit", "shell", "task_manager", "rollback")
WORKFLOW_NEEDLES = ("release", "rollback")
ACTION_NEEDLES = ("session_start", "handle_message")
FIELD_NEEDLES = ('"action"', '"workflow"', '"check"', '"tasks"')
RUNTIME_TIMEOUT = 60


def check(path: Path) -> list[str]:
    problems: list[str] = []
    if not path.exists():
        return [f"shell not found: {path}"]

    source = path.read_text(encoding="utf-8")

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax error: {exc}"]

    funcs = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    for name in ("session_start", "handle_message"):
        if name not in funcs:
            problems.append(f"missing function: {name}")

    for needle in PLUGIN_NEEDLES:
        if f'register_plugin("{needle}")' not in source:
            problems.append(f"missing register_plugin: {needle}")
    for needle in WORKFLOW_NEEDLES + ACTION_NEEDLES:
        if needle not in source:
            problems.append(f"missing name: {needle}")
    for needle in FIELD_NEEDLES:
        if needle not in source:
            problems.append(f"missing field: {needle}")

    if "$results" not in source and "safe_substitute" not in source:
        problems.append("missing Template rendering")

    return problems


def run_once() -> list[str]:
    """以子进程跑一次 `python main.py check`，验证入口真的能跑通。"""
    if not ENTRY.exists():
        return [f"entry not found: {ENTRY}"]
    try:
        proc = subprocess.run(  # noqa: S603 - 固定命令（本机 python + 本仓库入口）
            [sys.executable, str(ENTRY), "check"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=RUNTIME_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"runtime check could not start: {exc}"]

    if proc.returncode != 0:
        return [f"runtime check exit={proc.returncode}: {(proc.stderr or '').strip()[:200]}"]
    if "syntax ok" not in (proc.stdout or ""):
        return [f"runtime check output missing 'syntax ok': {(proc.stdout or '').strip()[:200]}"]
    return []


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else DEFAULT_SHELL
    if not target.is_absolute():
        target = ROOT / target

    problems = check(target)
    if not PLUGIN.exists():
        problems.append(f"plugin not found: {PLUGIN}")
    else:
        try:
            ast.parse(PLUGIN.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            problems.append(f"plugin syntax error: {exc}")

    runtime = run_once()
    problems.extend(runtime)

    if problems:
        print("shell check FAILED:")
        for item in problems:
            print("  - " + item)
        return 1

    print(f"shell check passed: {target.name} (syntax ok, contract complete)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
