"""Harness Engineering 成熟度审计（第 8 章 8.8）。

书本 8.8「从 Agent 设计模式到 Harness Engineering」的论述：

  > 有的工具从规范生成切入，有的从项目记忆、技能、子智能体或工作流编排切入，
  > 还有的把重点放在 Harness Engineering 上，即**围绕模型构建上下文、工具、
  > 权限、验证与反馈回路**。名字不同、入口不同，但都在试图回答同一个工程问题：
  > 怎样让 AI 在明确的边界内可靠地完成软件开发任务。

这段话给出了 harness 的**五个构成维度**：上下文 / 工具 / 权限 / 验证 / 反馈回路。

本模块把这五维做成**可审计的检查**：逐项检查仓库里是否真的具备对应构件，
输出成熟度报告与改进建议。

为什么这是有价值的产物而不是又一份总结：这个复现项目本身就是一个 harness
（CLAUDE.md 是上下文、sdd_agents 是工具、护栏是权限、测试是验证、
生成—评审是反馈回路）。审计工具能客观回答"它到底完备到什么程度"，
也能给任何别的项目用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class HarnessCheck:
    """一条 harness 检查项。"""

    key: str
    dimension: str  # 五个维度之一
    what: str  # 检查什么
    why: str  # 为什么这个维度重要
    relative_path: str  # 期望存在的构件
    # 构件位于项目上一级（工作区）时置 True。
    # 本复现工作区里，brownfield-demo 与 kb-search 是与主项目并列的独立项目。
    workspace_level: bool = False
    # 可选构件：缺失时只提示、不计入缺口（避免把环境差异误报成 harness 缺陷）
    optional: bool = False


@dataclass
class CheckResult:
    """单条检查的结果。"""

    check: HarnessCheck
    present: bool
    evidence: str = ""


@dataclass
class HarnessReport:
    """成熟度报告。"""

    results: list[CheckResult] = field(default_factory=list)

    @property
    def present_count(self) -> int:
        return sum(1 for r in self.results if r.present)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def score(self) -> float:
        return round(self.present_count / self.total, 3) if self.total else 0.0

    def by_dimension(self) -> dict[str, tuple[int, int]]:
        """每个维度的 (已具备, 总数)。"""
        summary: dict[str, list[int]] = {}
        for result in self.results:
            bucket = summary.setdefault(result.check.dimension, [0, 0])
            bucket[1] += 1
            if result.present:
                bucket[0] += 1
        return {k: (v[0], v[1]) for k, v in summary.items()}

    @property
    def missing(self) -> list[CheckResult]:
        """计入缺口的项（可选构件不计入）。"""
        return [r for r in self.results if not r.present and not r.check.optional]

    @property
    def optional_missing(self) -> list[CheckResult]:
        """缺失但属可选（环境差异，非缺陷）。"""
        return [r for r in self.results if not r.present and r.check.optional]

    @property
    def required_total(self) -> int:
        """必需构件总数（评分分母，排除可选构件）。"""
        return sum(1 for r in self.results if not r.check.optional)

    @property
    def required_present(self) -> int:
        return sum(1 for r in self.results if r.present and not r.check.optional)

    def summary(self) -> str:
        lines = [
            "Harness 成熟度审计（第 8 章 8.8）",
            "=" * 66,
            "  五个维度：上下文 / 工具 / 权限 / 验证 / 反馈回路",
        ]
        if self.required_total:
            ratio = self.required_present / self.required_total
            lines.append(
                f"  必需构件：{self.required_present}/{self.required_total} = {ratio:.0%}"
            )
        else:
            lines.append("  必需构件：0/0")
        lines.append("=" * 66)
        for dimension, (got, total) in self.by_dimension().items():
            mark = "✓" if got == total else ("!" if got else "✗")
            lines.append(f"  {mark} {dimension:<8} {got}/{total}")
        lines.append("")
        lines.append("  逐项明细：")
        for result in self.results:
            mark = "✓" if result.present else ("-" if result.check.optional else "✗")
            lines.append(f"    {mark} [{result.check.dimension}] {result.check.what}")
            if result.evidence:
                lines.append(f"        {result.evidence}")
        if self.missing:
            lines.append("")
            lines.append("  缺口与建议：")
            for result in self.missing:
                lines.append(f"    - 缺 {result.check.what}")
                lines.append(f"      为什么重要：{result.check.why}")
                lines.append(f"      建议补：{result.check.relative_path}")
        if self.optional_missing:
            lines.append("")
            lines.append("  可选构件缺失（环境差异，不计入缺口）：")
            for result in self.optional_missing:
                lines.append(f"    - {result.check.what}（{result.check.relative_path}）")
        return "\n".join(lines)


# 五维度的具体检查项。每项都对应书中 8.8 的论述方向。
CHECKS: list[HarnessCheck] = [
    # ---- 维度一：上下文（让 Agent 每次拿到同一套高信号背景）----
    HarnessCheck(
        key="project_context",
        dimension="上下文",
        what="项目上下文锚点（CLAUDE.md）",
        why="书第 3 章 3.1.4 第二层：上下文漂移是 AI 辅助编程的核心困难之一",
        relative_path="CLAUDE.md",
    ),
    HarnessCheck(
        key="spec_index",
        dimension="上下文",
        what="规范文件索引（specs/ 三份核心规范）",
        why="规范是第一手工件，必须让 Agent 每次都能读到",
        relative_path="specs/proposal.md",
    ),
    HarnessCheck(
        key="machine_readable_contract",
        dimension="上下文",
        what="机器可读的接口契约（api-spec.yaml）",
        why="书第 3 章 3.1：Spec-Kit 的 /speckit.analyze 做跨文档一致性检查；"
            "把契约写成机器可读形式，字段漂移才能被工具校验而不只是写在文档里",
        relative_path="specs/contracts/api-spec.yaml",
    ),
    # ---- 维度二：工具（Agent 能做什么）----
    HarnessCheck(
        key="agent_toolkit",
        dimension="工具",
        what="Agent 能力包（评审 / 路由 / 调度 / 度量）",
        why="书第 8 章：把方法论转化为可调用的工具，而非只留在文档中",
        relative_path="sdd_agents/__main__.py",
    ),
    HarnessCheck(
        key="brownfield_analyzer",
        dimension="工具",
        what="存量代码逆向工程分析器",
        why="书第 7.4 节：为存量系统补规范需要可复用的分析能力，第一步逆向工程应由工具承担",
        relative_path="brownfield-demo/tools/brownfield.py",
        workspace_level=True,
        # 构件可能不在当前工作区（例如把本项目单独拷出去用），
        # 缺失只提示不判错 —— 否则审计工具会把"环境差异"误报成"harness 缺陷"。
        optional=True,
    ),
    HarnessCheck(
        key="subagent_scheduler",
        dimension="工具",
        what="子智能体并行调度器（DAG 分波）",
        why="书第 6.7 节：并行执行需要调度能力，而非人工排队",
        relative_path="sdd_agents/orchestrator.py",
    ),
    # ---- 维度三：权限（边界与红线）----
    HarnessCheck(
        key="input_guard",
        dimension="权限",
        what="入护栏（写文件前拦截越界）",
        why="书第 8.4 节：没有负面约束，AI 会把相关功能就近塞进当前模块",
        relative_path="scripts/input_guard.py",
    ),
    HarnessCheck(
        key="output_guard",
        dimension="权限",
        what="出护栏（执行后自动验证）",
        why="书第 8.4 节：护栏三明治需要出口一侧",
        relative_path="scripts/output_guard.py",
    ),
    HarnessCheck(
        key="hooks_config",
        dimension="权限",
        what="Hooks 注册（在 Claude Code 中挂载护栏）",
        why="书第 8.4.3 节：护栏必须真的被挂载，否则只是躺在仓库里的脚本",
        relative_path=".claude/settings.json",
    ),
    # ---- 维度四：验证（怎么算做对了）----
    HarnessCheck(
        key="test_suite",
        dimension="验证",
        what="测试套件（验收标准 → 测试用例）",
        why="书第 7.1 节：一条验收标准至少一个测试用例",
        relative_path="tests",
    ),
    HarnessCheck(
        key="contract_tests",
        dimension="验证",
        what="契约一致性测试（规范 ↔ 实现）",
        why="书第 3 章：Spec-Kit 的 /speckit.analyze 做的正是这件事",
        relative_path="tests/test_contracts.py",
    ),
    HarnessCheck(
        key="spec_ci",
        dimension="验证",
        what="规范 CI（多层自动检查）",
        why="书第 9.6 节：规范审查不能只靠人",
        relative_path=".github/workflows/spec-check.yml",
    ),
    # ---- 维度五：反馈回路（越用越准）----
    HarnessCheck(
        key="generate_review",
        dimension="反馈回路",
        what="生成—评审回路（自动挑错并要求修正）",
        why="书第 8.2 节：评审依据客观标准，不合格则要求修正后重来",
        relative_path="sdd_agents/generate_review.py",
    ),
    HarnessCheck(
        key="metrics",
        dimension="反馈回路",
        what="规范度量（验收覆盖率 / 测试兑现度）",
        why="书第 9.5 节：度量是改进的前提，且必须能自动采集",
        relative_path="sdd_agents/metrics.py",
    ),
    HarnessCheck(
        key="governance",
        dimension="反馈回路",
        what="治理检查（ADR 只追加 / 模板底线 / 同步提交）",
        why="书第 9 章：规范治理要能自动检查，否则约束会随时间失效",
        relative_path="sdd_agents/workflow.py",
    ),
]


def find_workspace(start: str | Path | None = None) -> Path:
    """定位工作区根目录：包含 brownfield-demo 或 kb-search 的那一层。

    为什么不直接用 `root.parent`：项目可能被单独拷到别处运行，
    此时上一级并不是工作区。找不到就回落到 `root` 本身 ——
    这样带 workspace_level 的检查会退化为"本项目内找不到"，报为可选缺失，
    而不是把整次审计判成失败。
    """
    base = Path(start) if start is not None else Path(__file__).resolve().parent.parent
    candidates = [base, base.parent, base.parent.parent]
    for candidate in candidates:
        if (candidate / "brownfield-demo").exists() or (candidate / "kb-search").exists():
            return candidate
    return base


def _search_roots(base: Path, workspace: Path, check: HarnessCheck) -> list[Path]:
    """给出某个检查项可能所在的候选根目录。

    规则要保持"最小惊讶"：
      - 带 workspace_level 的跨项目构件 → 工作区根、以及 base 本身
      - 其余构件 → **只查 base**
      - 仅当 base 恰好就是工作区根时，才额外下探到 sdd-daily-report/

    最后一条是关键：从工作区根审计时需要下探，但**不能**在 base 已经是
    某个项目或某个临时子目录时也去下探真实项目 —— 否则"审计一个空目录"
    会错误地报告为"全部具备"。
    """
    if check.workspace_level:
        return [workspace, base]

    roots = [base]
    project_dir = workspace / "sdd-daily-report"
    if base.resolve() == workspace.resolve() and project_dir.exists():
        roots.append(project_dir)
    return roots


def audit(root: str | Path) -> HarnessReport:
    """审计一个项目的 harness 成熟度。

    检查项以**文件是否真实存在**为准 —— 不读取内容、不做主观判断，
    因此结果可复现、不会因为措辞变化而波动。

    同时支持"在项目内审计"与"在工作区根审计"两种调用（见 `_search_roots`）。
    """
    base = Path(root)
    workspace = find_workspace(base)
    results: list[CheckResult] = []
    for check in CHECKS:
        target = None
        for search_root in _search_roots(base, workspace, check):
            candidate = (search_root / check.relative_path).resolve()
            if candidate.exists():
                target = candidate
                break

        present = target is not None
        evidence = ""
        if target is not None:
            if target.is_dir():
                count = len(list(target.rglob("*.py")))
                evidence = f"存在：{check.relative_path}/（{count} 个 .py）"
            else:
                lines = len(target.read_text(encoding="utf-8", errors="replace").splitlines())
                evidence = f"存在：{check.relative_path}（{lines} 行）"
        results.append(CheckResult(check=check, present=present, evidence=evidence))
    return HarnessReport(results=results)


def audit_workspace(root: str | Path) -> HarnessReport:
    """审计整个工作区（覆盖主项目 + 案例项目 + Brownfield 演示）。"""
    return audit(root)
