"""一键运行工作区内所有项目的测试。

背景：三个项目各自有名为 `tests` 的测试包，pytest 在仓库根统一收集会模块名冲突，
因此必须**按项目分别启动 pytest 进程**。本脚本做的就是这件事，并汇总结果。

用法：
    python run_all_tests.py            # 跑全部
    python run_all_tests.py -v         # 透传 -v 给各项目
    python run_all_tests.py --skip-brownfield
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# (项目目录, 展示名, 是否有真实测试套件)
PROJECTS: list[tuple[str, str]] = [
    ("sdd-daily-report", "主项目：智能日报生成器"),
    ("kb-search", "案例项目：知识库语义搜索工具"),
    ("brownfield-demo", "Brownfield 四步法"),
]


@dataclass
class Result:
    name: str
    returncode: int
    summary: str
    duration: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _force_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover
                pass


def run_project(directory: str, name: str, extra: list[str]) -> Result:
    project_dir = ROOT / directory
    if not project_dir.exists():
        return Result(name, 1, f"目录不存在：{directory}", 0.0)
    if not (project_dir / "tests").exists():
        return Result(name, 0, "无测试目录，跳过", 0.0)

    started = time.perf_counter()
    # 不加 -q：-q 会抑制 "N passed" 汇总行，导致无法一眼看出用例数
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *extra],
        cwd=project_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    duration = time.perf_counter() - started

    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    # 优先取含 "passed"/"failed"/"error" 的汇总行
    summary = ""
    for line in reversed(lines):
        if any(word in line for word in ("passed", "failed", "error", "no tests ran")):
            summary = line
            break
    if not summary:
        summary = lines[-1] if lines else "(无输出)"
    return Result(name, proc.returncode, summary, duration)


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    parser = argparse.ArgumentParser(description="运行工作区内所有项目的测试")
    parser.add_argument("-v", "--verbose", action="store_true", help="透传 -v 给 pytest")
    parser.add_argument("--skip-brownfield", action="store_true", help="跳过 Brownfield 演示")
    args = parser.parse_args(argv)

    extra = ["-v"] if args.verbose else []
    results: list[Result] = []

    for directory, name in PROJECTS:
        if args.skip_brownfield and directory == "brownfield-demo":
            continue
        print(f"▶ {name}（{directory}）…", flush=True)
        result = run_project(directory, name, extra)
        results.append(result)
        mark = "✓" if result.ok else "✗"
        print(f"  {mark} {result.summary}  [{result.duration:.2f}s]")

    print()
    print("=" * 62)
    passed = sum(1 for r in results if r.ok)
    total = len(results)
    total_time = sum(r.duration for r in results)
    if passed == total:
        print(f"全部通过：{passed}/{total} 个项目，合计 {total_time:.2f}s")
        return 0
    print(f"存在失败：{passed}/{total} 个项目通过")
    for result in results:
        if not result.ok:
            print(f"  ✗ {result.name}：{result.summary}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
