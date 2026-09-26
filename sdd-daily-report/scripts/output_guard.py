"""出护栏：AI 修改文件之后自动跑测试（护栏三明治模式，第 8 章 8.6.3）。

规则：只要发生代码写入，就跑一次全量测试；测试失败则拦截并要求修复。

用法（在 Claude Code Hooks 的 PostToolUse 中调用）：
    退出码 0 = 通过，2 = 测试失败需修复
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run_pytest() -> tuple[int, str]:
    """运行全量测试，返回 (退出码, 输出尾段)。"""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output


def main() -> int:
    code, output = run_pytest()
    tail = "\n".join(output.strip().splitlines()[-15:])
    if code != 0:
        print("出护栏拦截：测试未通过，请先修复再继续。", file=sys.stderr)
        print(tail, file=sys.stderr)
        return 2
    print(f"出护栏通过：{tail.splitlines()[-1] if tail else 'pytest 全绿'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
