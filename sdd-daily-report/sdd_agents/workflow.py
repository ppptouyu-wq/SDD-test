"""团队协作与规范治理（第 9 章）。

落地内容：
  - 9.1 从个人到团队的三个阶段 + 阶段准入条件
  - 9.2 规范的协作模式：三种角色（Spec Author / Reviewer / Executor）与 PR 流程
  - 9.3 模板的团队标准化：三条底线（模板见 team-templates/）
  - 9.6 CI/CD 中的规范守护：三个层次的自动化检查

设计原则：本章大部分是**组织流程**，无法用代码替代。因此本模块只做两件事：
  1. 把"能自动化的治理规则"变成可执行检查（PR 范围、ADR 只追加、模板底线）
  2. 把"不能自动化的人类流程"固化成结构化清单，避免口头约定流失
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# ===================================================== 9.1 三个阶段

class Stage(str, Enum):
    """团队引入 SDD 的三个阶段（书 9.1.1：为什么不能一步到位）。"""

    PILOT = "第一阶段：试点"
    TEAM = "第二阶段：团队推广"
    ORG = "第三阶段：组织级"


@dataclass
class StageGate:
    """阶段推进的准入条件。不满足就不要进入下一阶段。"""

    stage: Stage
    requirements: list[str]
    failure_signal: str


STAGE_GATES: list[StageGate] = [
    StageGate(
        stage=Stage.PILOT,
        requirements=[
            "选择 1~2 名愿意尝试的试点人（书 9.1.2：选对试点人）",
            "选择 1 个规模可控、需求相对稳定的试点项目",
            "试点人要同时具备架构判断力与推进意愿，而不是「最闲的人」",
        ],
        failure_signal="试点人被指派而非自愿 -> 遇阻即放弃，流程推广失败",
    ),
    StageGate(
        stage=Stage.TEAM,
        requirements=[
            "试点项目已完成至少一次真实迭代（含规范回溯修正）",
            "沉淀出团队模板（至少 proposal/design/tasks 三份）",
            "明确三种角色的分工与 PR 审查流程",
            "规范 CI 已接入，能自动拦截规范漂移",
        ],
        failure_signal="试点成功但无人负责沉淀模板 -> 每个项目各写各的，评审无共同语言",
    ),
    StageGate(
        stage=Stage.ORG,
        requirements=[
            "至少两个团队独立跑通，且度量指标可比",
            "规范模板进入组织级基线仓库",
            "规范审查已纳入现有评审流程（而非另起炉灶）",
        ],
        failure_signal="只在团队内自转，未与既有流程融合 -> 双轨制，最终被放弃",
    ),
]


def next_stage(current: Stage) -> Stage | None:
    order = [Stage.PILOT, Stage.TEAM, Stage.ORG]
    idx = order.index(current)
    return order[idx + 1] if idx + 1 < len(order) else None


def stage_readiness(current: Stage, satisfied: list[str]) -> dict:
    """检查某阶段的准入条件满足情况。"""
    gate = next((g for g in STAGE_GATES if g.stage == current), None)
    if gate is None:
        raise ValueError(f"未知阶段：{current}")
    missing = [r for r in gate.requirements if r not in satisfied]
    return {
        "stage": gate.stage.value,
        "ready": not missing,
        "missing": missing,
        "failure_signal": gate.failure_signal,
    }


# =============================================== 9.2 三种角色

class Role(str, Enum):
    """规范协作的三种角色（书 9.2.1）。"""

    AUTHOR = "Spec Author（撰写者）"
    REVIEWER = "Spec Reviewer（审查者）"
    EXECUTOR = "Spec Executor（执行者）"


ROLE_DUTIES: dict[Role, dict[str, list[str]]] = {
    Role.AUTHOR: {
        "职责": [
            "撰写 proposal.md / design.md / tasks.md",
            "确保验收标准可测试",
            "响应审查意见并更新规范",
        ],
        "不负责": ["不批准自己的规范", "不跳过审查直接开工"],
    },
    Role.REVIEWER: {
        "职责": [
            "审查规范的完整性与可测试性",
            "检查是否越界（实现了不做的事）",
            "确认接口契约与实现一致",
        ],
        "不负责": ["不替作者改写规范", "不审查自己撰写的规范"],
    },
    Role.EXECUTOR: {
        "职责": [
            "按 tasks.md 逐任务执行",
            "同时产出实现与测试",
            "发现规范缺陷时**回溯修改规范**，而不是在代码里打补丁",
        ],
        "不负责": ["不擅自扩大范围", "不在未更新规范的情况下改契约"],
    },
}


def separation_of_duties(author: str, reviewer: str) -> tuple[bool, str]:
    """职责分离：作者不能审查自己的规范（书 9.2.1 的隐含约束）。"""
    if not author or not reviewer:
        return False, "作者与审查者都必须指定"
    if author == reviewer:
        return False, "作者不能审查自己撰写的规范（职责分离）"
    return True, "职责分离通过"


# =============================================== 9.6 规范 CI 三层

@dataclass
class CiLayer:
    """规范 CI 的一个层次（书 9.6.2：三个层次的自动化规范检查）。"""

    name: str
    what: str
    why: str


CI_LAYERS: list[CiLayer] = [
    CiLayer(
        name="层次 1：规范存在性与完整性",
        what="核心规范文件齐备；proposal.md 含三层结构与可测试的验收清单",
        why="规范缺失是最容易发生、也最容易检查的漂移",
    ),
    CiLayer(
        name="层次 2：规范与代码一致性",
        what="design.md 契约中的每个函数在实现中都存在，参数名一致",
        why="接口契约漂移会导致模块无法对接，且往往到集成时才暴露",
    ),
    CiLayer(
        name="层次 3：术语与范围合规",
        what="不得实现 proposal.md 排除的功能；不得硬编码密钥",
        why="AI 最容易「好心」加功能，必须在合并前拦住",
    ),
]


def ci_layer_report() -> str:
    lines = ["规范 CI 的三个层次（第 9.6.2 节）", "=" * 56]
    for i, layer in enumerate(CI_LAYERS, 1):
        lines.append(f"{layer.name}")
        lines.append(f"    检查什么：{layer.what}")
        lines.append(f"    为什么：  {layer.why}")
        if i < len(CI_LAYERS):
            lines.append("")
    lines.append("=" * 56)
    lines.append("实现文件：.github/workflows/spec-check.yml")
    return "\n".join(lines)


# ============================================ 治理规则的可执行检查

@dataclass
class GovernanceFinding:
    rule: str
    severity: str  # "error" | "warning"
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.rule}：{self.message}"


@dataclass
class GovernanceReport:
    findings: list[GovernanceFinding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    def summary(self) -> str:
        head = "✓ 治理检查通过" if self.passed else "✗ 治理检查未通过"
        lines = [head]
        lines += [f"  {f}" for f in self.findings]
        return "\n".join(lines)


def check_adr_append_only(adr_dir: str | Path) -> list[GovernanceFinding]:
    """ADR 只追加、不修改（第 7 章 7.3.1 原则 3）：编号必须连续且不重复。"""
    findings: list[GovernanceFinding] = []
    directory = Path(adr_dir)
    if not directory.exists():
        return [GovernanceFinding("ADR 治理", "warning", f"ADR 目录不存在：{directory}")]

    numbers: list[int] = []
    for path in sorted(directory.glob("*.md")):
        match = re.match(r"(\d+)", path.name)
        if not match:
            findings.append(
                GovernanceFinding(
                    "ADR 治理", "warning", f"{path.name} 未以编号开头，不利于追溯"
                )
            )
            continue
        numbers.append(int(match.group(1)))

    if numbers:
        expected = list(range(1, len(numbers) + 1))
        if sorted(numbers) != expected:
            findings.append(
                GovernanceFinding(
                    "ADR 治理",
                    "error",
                    f"ADR 编号不连续：实际 {sorted(numbers)}，期望 {expected}。"
                    "只追加原则要求旧编号不被删除或改写",
                )
            )
        if len(set(numbers)) != len(numbers):
            findings.append(
                GovernanceFinding("ADR 治理", "error", "ADR 编号重复（可能覆盖了旧决策）")
            )
    return findings


def check_template_baseline(spec_path: str | Path) -> list[GovernanceFinding]:
    """模板三条底线（team-templates/README.md）：不做什么 / 验收可测 / 关联规范。"""
    findings: list[GovernanceFinding] = []
    path = Path(spec_path)
    text = path.read_text(encoding="utf-8")

    if "proposal" in path.stem.lower():
        if not re.search(r"不做什么|明确排除", text):
            findings.append(
                GovernanceFinding("模板底线", "error", f"{path.name} 缺少「不做什么」")
            )
        if not re.search(r"^\s*-\s*\[[ x]\]", text, flags=re.MULTILINE):
            findings.append(
                GovernanceFinding(
                    "模板底线", "error", f"{path.name} 验收标准不是可勾选清单"
                )
            )
    if "design" in path.stem.lower() and "proposal.md" not in text:
        findings.append(
            GovernanceFinding(
                "模板底线", "warning", f"{path.name} 未标注上游规范 proposal.md"
            )
        )
    if "tasks" in path.stem.lower() and "design.md" not in text:
        findings.append(
            GovernanceFinding(
                "模板底线", "warning", f"{path.name} 未关联 design.md"
            )
        )
    return findings


def check_commit_scope(repo: str | Path = ROOT) -> list[GovernanceFinding]:
    """规范与代码同步提交（第 7 章 7.3.1 原则 1）。

    检查最近一次提交：若改动了代码却没动 specs/，则提示可能是"规范没跟上"。
    无法读取 git 历史时降级为 warning，不阻断。
    """
    try:
        proc = subprocess.run(
            ["git", "show", "--name-only", "--pretty=format:", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [GovernanceFinding("同步提交", "warning", f"无法读取 git 历史：{exc}")]

    if proc.returncode != 0:
        return [
            GovernanceFinding(
                "同步提交", "warning", "当前不是 git 仓库或没有提交，跳过该检查"
            )
        ]

    changed = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    touched_specs = any(p.startswith("specs/") for p in changed)
    touched_code = any(p.endswith(".py") for p in changed)

    if touched_code and not touched_specs:
        return [
            GovernanceFinding(
                "同步提交",
                "warning",
                "最近一次提交改了代码但未改 specs/ —— 若属于「哪种变更都算」的改动可忽略，"
                "否则说明规范没跟上（第 7 章 7.3.1 原则 1）",
            )
        ]
    return []


def run_governance_checks(project_root: str | Path = ROOT) -> GovernanceReport:
    """跑一遍所有可自动化的治理检查。"""
    base = Path(project_root)
    findings: list[GovernanceFinding] = []
    findings += check_adr_append_only(base / "specs" / "adrs")
    for name in ("proposal.md", "design.md", "tasks.md"):
        spec = base / "specs" / name
        if spec.exists():
            findings += check_template_baseline(spec)
    findings += check_commit_scope(base)
    return GovernanceReport(findings=findings)
