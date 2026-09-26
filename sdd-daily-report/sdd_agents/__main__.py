"""SDD Agent 模式命令行入口。

用法：
    python -m sdd_agents review            # 生成—评审：审查 specs/ 下的规范
    python -m sdd_agents route             # 条件路由：按 design.md 把任务分发给执行者
    python -m sdd_agents guard             # 护栏三明治：自检入护栏是否拦得住
    python -m sdd_agents delegate [--wave] # 层级委托：按 tasks.md 的 DAG 分波并行执行
    python -m sdd_agents all               # 依次执行以上全部
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from sdd_agents.generate_review import review_many
from sdd_agents.guardrail import run_guardrails
from sdd_agents.framework import recommend, render_decision_tree
from sdd_agents.harness import audit as audit_harness
from sdd_agents.metrics import measure_project, render_metrics
from sdd_agents.orchestrator import Orchestrator, PytestVerifier, load_tasks, summarise
from sdd_agents.router import build_route_table, group_by_handler, route_all
from sdd_agents.suitability import (
    CRITERIA,
    Answer,
    assess,
    render_boundary_reference,
)
from sdd_agents.workflow import (
    ROLE_DUTIES,
    STAGE_GATES,
    ci_layer_report,
    run_governance_checks,
)


def _force_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover
                pass


def cmd_review(_args) -> int:
    """生成—评审模式：按第 4 章检查清单审查规范。"""
    specs = [
        ROOT / "specs" / "proposal.md",
        ROOT / "specs" / "design.md",
        ROOT / "specs" / "tasks.md",
    ]
    print("=" * 60)
    print("生成—评审模式：规范审查（第 8 章 8.2）")
    print("=" * 60)
    results = review_many(specs)
    for result in results:
        print(result.summary())
        print()
    failed = [r for r in results if not r.passed]
    print("=" * 60)
    if failed:
        print(f"审查未通过：{len(failed)} 份规范存在错误级问题")
        return 1
    print("审查通过：三份规范均满足第 4 章规范质量检查清单")
    return 0


def cmd_route(_args) -> int:
    """条件路由模式：design.md 的模块边界即路由表。"""
    print("=" * 60)
    print("条件路由模式：按规范分发任务（第 8 章 8.5）")
    print("=" * 60)
    table = build_route_table()
    print("路由表（来自 specs/design.md §2 模块职责定义）：")
    for module, handler in table.module_to_handler.items():
        print(f"  {module + '/':<16} → {handler}")
    print(f"  {'(兜底)':<16} → {table.fallback}-agent")
    print()

    tasks = load_tasks()
    decisions = route_all(tasks, table=table)
    print(f"共 {len(tasks)} 个任务已分发：")
    for decision in decisions:
        print(f"  {decision}")
    print()

    grouped = group_by_handler(decisions)
    print("按执行者分组（同组内可并行）：")
    for handler, items in sorted(grouped.items()):
        ids = ", ".join(d.task_id for d in items)
        print(f"  {handler:<18} {len(items)} 个任务：{ids}")
    return 0


def cmd_guard(_args) -> int:
    """护栏三明治模式：自检。"""
    print("=" * 60)
    print("护栏三明治模式：入/出护栏自检（第 8 章 8.4）")
    print("=" * 60)
    report = run_guardrails(include_output_guard=False)
    print(report.summary())
    return 0 if report.passed else 1


def cmd_delegate(args) -> int:
    """层级委托 + 子智能体并行：按 tasks.md 的 DAG 分波执行。"""
    tasks = load_tasks()
    worker = PytestVerifier()
    orchestrator = Orchestrator(
        tasks=tasks,
        worker=worker,
        max_workers=args.max_workers,
    )
    print("=" * 60)
    print("层级委托模式：tasks.md 驱动分波并行（第 8 章 8.3 / 第 6 章 6.7）")
    print("=" * 60)
    waves = orchestrator.plan()
    print(f"管理者解析出 {len(waves)} 个波次，最大并行度上限 {args.max_workers}：")
    for i, wave in enumerate(waves, start=1):
        ids = ", ".join(t.task_id for t in wave)
        print(f"  第 {i} 波（{len(wave)} 个）：{ids}")
    print()

    if args.wave:
        print("（--wave 模式：只展示调度计划，不执行；避免每个任务都跑一遍全量测试）")
        return 0

    results = orchestrator.dispatch()
    print(summarise(results))
    return 0 if all(r.ok for r in results if r.task_id != "(scheduler)") else 1


def cmd_team(args) -> int:
    """团队实践（第 9 章）：阶段准入、角色分工、治理检查、CI 层次。"""
    print("=" * 62)
    print("一、团队引入 SDD 的三个阶段（第 9.1 节）")
    print("=" * 62)
    for gate in STAGE_GATES:
        print(f"\n{gate.stage.value} —— 准入条件：")
        for req in gate.requirements:
            print(f"  - {req}")
        print(f"  失败信号：{gate.failure_signal}")

    print()
    print("=" * 62)
    print("二、规范的协作模式：三种角色（第 9.2.1 节）")
    print("=" * 62)
    for role, duties in ROLE_DUTIES.items():
        print(f"\n{role.value}")
        for label, items in duties.items():
            print(f"  {label}：")
            for item in items:
                print(f"    - {item}")

    print()
    print("=" * 62)
    print("三、规范 CI 的三个层次（第 9.6.2 节）")
    print("=" * 62)
    print(ci_layer_report())

    print()
    print("=" * 62)
    print("四、可自动化的治理检查")
    print("=" * 62)
    print(run_governance_checks().summary())
    return 0


def cmd_metrics(args) -> int:
    """规范度量（第 9.5 节）。"""
    print(render_metrics(measure_project(ROOT)))
    return 0


def cmd_suitability(args) -> int:
    """SDD 适用性自评（第 10 章 10.1：SDD 的边界）。"""
    if getattr(args, "answers", None):
        tokens = [t.strip().lower() for t in args.answers.split(",")]
        if len(tokens) != len(CRITERIA):
            print(f"需要 {len(CRITERIA)} 个作答（对应 {len(CRITERIA)} 条标准），"
                  f"实际给了 {len(tokens)} 个")
            return 2
        answers = [
            Answer(key=c.key, yes=t in ("y", "yes", "1", "true", "是"))
            for c, t in zip(CRITERIA, tokens)
        ]
        print(assess(answers).summary())
        return 0

    print("=" * 64)
    print("SDD 适用性自评（第 10 章 10.1）")
    print("=" * 64)
    print(f"\n请回答以下 {len(CRITERIA)} 个问题，按顺序用逗号分隔传入 yes/no：\n")
    for i, c in enumerate(CRITERIA, 1):
        print(f"  {i}. {c.question}")
    print(f"\n示例：python -m sdd_agents suitability "
          f"--answers {','.join(['yes'] * len(CRITERIA))}")
    print()
    print(render_boundary_reference())
    return 0


def cmd_framework(args) -> int:
    """SDD 框架选型（第 3 章 3.1 / 图 3-7 决策树）。"""
    # 未提供任何场景参数时只打印决策树
    if not args.solo and not args.team:
        print(render_decision_tree())
        print()
        print("用法示例：")
        print("  python -m sdd_agents framework --solo")
        print("  python -m sdd_agents framework --team --size 3 --cicd yes")
        print("  python -m sdd_agents framework --team --size 8")
        return 0

    try:
        if args.solo:
            result = recommend(is_team=False)
        else:
            needs_cicd = None
            if args.cicd is not None:
                needs_cicd = args.cicd.lower() in ("y", "yes", "1", "true", "是")
            result = recommend(
                is_team=True, team_size=args.size, needs_cicd=needs_cicd
            )
    except ValueError as exc:
        print(f"参数不足或不合法：{exc}")
        return 2

    print(result.summary())
    return 0


def cmd_harness(args) -> int:
    """Harness 成熟度审计（第 8 章 8.8）。

    默认审计本项目；`--workspace` 从工作区根审计（含案例项目与 Brownfield 演示）。
    """
    from sdd_agents.harness import find_workspace

    target = find_workspace(ROOT) if args.workspace else ROOT
    report = audit_harness(target)
    print(report.summary())
    return 0 if not report.missing else 1


def cmd_all(args) -> int:
    codes = [
        cmd_review(args),
        cmd_route(args),
        cmd_guard(args),
    ]
    return max(codes)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SDD Agent 设计模式落地工具")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("review", help="生成—评审：审查 specs/ 下的规范")
    sub.add_parser("route", help="条件路由：按 design.md 分发任务")
    sub.add_parser("guard", help="护栏三明治：入护栏自检")

    delegate = sub.add_parser("delegate", help="层级委托：按 DAG 分波并行执行")
    delegate.add_argument(
        "--max-workers", type=int, default=3, help="最大并行度（书图 6-4 为 3）"
    )
    delegate.add_argument(
        "--wave", action="store_true", help="只打印调度计划，不执行"
    )

    sub.add_parser("team", help="团队实践：三阶段、三角色、治理检查")
    sub.add_parser("metrics", help="规范度量：验收覆盖率、测试兑现度")

    suitability = sub.add_parser("suitability", help="适用性自评（第 10 章边界）")
    suitability.add_argument(
        "--answers", help=f"{len(CRITERIA)} 个 yes/no，逗号分隔；省略则只打印标准"
    )

    framework = sub.add_parser("framework", help="框架选型（第 3 章图 3-7 决策树）")
    framework.add_argument("--solo", action="store_true", help="个人项目")
    framework.add_argument("--team", action="store_true", help="团队协作")
    framework.add_argument("--size", type=int, help="团队人数")
    framework.add_argument("--cicd", help="是否需要 CI/CD 自动化（yes/no）")

    harness = sub.add_parser("harness", help="Harness 成熟度审计（第 8 章 8.8）")
    harness.add_argument(
        "--workspace", action="store_true", help="审计整个工作区而非仅本项目"
    )

    sub.add_parser("all", help="依次执行 review / route / guard")
    return parser


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    args = build_parser().parse_args(argv)
    handlers = {
        "review": cmd_review,
        "route": cmd_route,
        "guard": cmd_guard,
        "delegate": cmd_delegate,
        "team": cmd_team,
        "metrics": cmd_metrics,
        "suitability": cmd_suitability,
        "framework": cmd_framework,
        "harness": cmd_harness,
        "all": cmd_all,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
