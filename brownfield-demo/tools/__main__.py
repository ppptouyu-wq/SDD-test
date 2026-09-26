"""Brownfield 命令行入口：把第 7.4.3 节四步法变成可执行流程。

用法：
    python -m tools                  # 走完四步，打印每步的产物
    python -m tools analyze          # 第一步：逆向工程（客观事实）
    python -m tools contract         # 第二步：生成接口契约草稿
    python -m tools prompts          # 打印四步法用到的提示词
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.brownfield import analyze_tree, render_interface_contract
from tools.prompts import (
    build_human_review_prompt,
    build_reverse_engineering_prompt,
    build_test_prompt,
)

LEGACY_DIR = ROOT / "legacy"
SPEC_PATH = ROOT / "specs" / "contracts" / "legacy-baseline.md"


def _force_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover
                pass


def _analyses():
    return [a for a in analyze_tree(LEGACY_DIR) if a.public_functions]


def cmd_analyze(_args) -> int:
    print("=" * 66)
    print("第一步：逆向工程 —— 只提取客观事实，不做价值判断")
    print("=" * 66)
    for analysis in _analyses():
        print(f"\n模块 {analysis.path}")
        print(f"  模块级文档字符串：{'有' if analysis.module_docstring else '❌ 无'}")
        print(f"  公开接口：{', '.join(analysis.public_functions) or '（无）'}")
        print(f"  外部依赖：{', '.join(sorted(set(analysis.imports)))}")
        print(f"  未写文档的成员：{len(analysis.undocumented)} 个")
        print(f"  识别出的隐性决策：{len(analysis.implicit_decisions)} 条")
        for decision in analysis.implicit_decisions:
            print(f"    - {decision}")
        if analysis.boundary_flags:
            print("  需人工确认的边界行为：")
            for flag in analysis.boundary_flags:
                print(f"    ! {flag}")
    return 0


def cmd_contract(_args) -> int:
    print("=" * 66)
    print("第二步：规范化 —— 生成接口契约草稿（必须人工审查后才能定稿）")
    print("=" * 66)
    print()
    for analysis in _analyses():
        module_name = Path(analysis.path).stem
        print(render_interface_contract(analysis, module_name=module_name))
        print()
    print("-" * 66)
    print(f"人工审查结论已定稿于：{SPEC_PATH.relative_to(ROOT)}")
    print("（本工具只生成草稿；'现有行为不等于正确行为'，判断权必须留给人）")
    return 0


def cmd_prompts(_args) -> int:
    print("=" * 66)
    print("四步法用到的提示词")
    print("=" * 66)
    modules = [a.path for a in _analyses()]
    sample = modules[0] if modules else "legacy/collector/github.py"

    print("\n【第一步 · 逆向工程】书中原文 Prompt：\n")
    print(build_reverse_engineering_prompt(sample))
    print("\n【第二步 · 人工审查】引导人逐条判断：\n")
    print(build_human_review_prompt(sample))
    print("\n【第三步 · 补写测试】：\n")
    print(build_test_prompt(sample))
    return 0


def cmd_all(args) -> int:
    codes = [cmd_analyze(args), cmd_contract(args)]
    print()
    print("=" * 66)
    print("第三步：补测试")
    print("=" * 66)
    print("  特征化测试：tests/test_legacy_characterization.py（固化现有行为）")
    print("  契约测试：  tests/test_legacy_fixed_contract.py（验证修正后行为）")
    print()
    print("=" * 66)
    print("第四步：锁定基线")
    print("=" * 66)
    print(f"  契约已提交：{SPEC_PATH.relative_to(ROOT)}")
    print("  此后该模块遵循'先更新规范，再修改代码'")
    print("  未变更的模块按第 7.4.2 节'逐步收缩'策略暂不处理")
    return max(codes)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Brownfield 四步法工具（第 7.4.3 节）",
        epilog="不指定子命令时等价于 all。",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("analyze", help="第一步：逆向工程")
    sub.add_parser("contract", help="第二步：生成接口契约草稿")
    sub.add_parser("prompts", help="打印四步法的提示词")
    sub.add_parser("all", help="analyze + contract + 步骤说明（默认）")
    return parser


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    args = build_parser().parse_args(argv)
    command = args.command or "all"
    handlers = {
        "analyze": cmd_analyze,
        "contract": cmd_contract,
        "prompts": cmd_prompts,
        "all": cmd_all,
    }
    return handlers[command](args)


if __name__ == "__main__":
    sys.exit(main())
