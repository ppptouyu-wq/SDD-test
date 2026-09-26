"""生成—评审模式（第 8 章 8.2）：规范审查的自动化。

书中要点：
  - 8.2.1 模式简介：生成者产出，评审者按**客观标准**挑错，不合格则要求修正后重来。
  - 8.2.2 SDD 为评审提供客观标准：评审依据不是"我觉得"，而是 proposal.md 的验收标准、
    design.md 的接口契约、以及第 4 章的规范质量检查清单。
  - 8.2.3 实践应用：一条指令启动自我审查循环（对应本项目 .claude 的 Hooks）。

本实现把"评审标准"固化为可执行的规则，因此**不需要 LLM 也能真跑**；
需要 LLM 时由 `llm_reviewer` 注入（保持可替换）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Literal

Severity = Literal["error", "warning"]

# 第 4 章 4.6 规范质量检查清单（表 4-2）的 8 条
CHECKLIST = [
    ("目标明确", "这份规范说明了为什么要做吗？"),
    ("范围有界", "做什么、不做什么都写清楚了吗？"),
    ("验收可测", "每条验收标准都能转成测试用例吗？"),
    ("性能有数", "性能要求有具体数字吗？"),
    ("边界覆盖", "异常与边界场景写了吗？"),
    ("无实现细节", "是否混入了本应属于 design.md 的实现细节？"),
    ("术语一致", "同一概念是否始终用同一个词？"),
    ("AI 可执行", "AI 读完能直接干活、不需要猜吗？"),
]


@dataclass
class ReviewFinding:
    """一条评审意见。"""

    rule: str  # 对应检查清单的条目名
    severity: Severity
    message: str
    line: int | None = None
    evidence: str = ""

    def __str__(self) -> str:
        loc = f"（第 {self.line} 行）" if self.line else ""
        return f"[{self.severity.upper()}] {self.rule}{loc}：{self.message}"


@dataclass
class ReviewResult:
    """评审结果。"""

    target: str
    findings: list[ReviewFinding] = field(default_factory=list)

    @property
    def errors(self) -> list[ReviewFinding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[ReviewFinding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def passed(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        if self.passed:
            head = f"✓ {self.target} 通过规范审查"
        else:
            head = f"✗ {self.target} 未通过（{len(self.errors)} 个错误）"
        lines = [head]
        lines += [f"  {f}" for f in self.findings]
        if self.passed and self.warnings:
            lines.append(f"  （{len(self.warnings)} 条提醒，不阻断）")
        return "\n".join(lines)


# ---------------------------------------------------------------- 规则集
# 每条规则是 (检查项名, 函数)；函数返回 findings 列表。

# 每份规范的**必查项**（第 4 章规范质量检查清单按文档类型细化）
# 关键点：三份规范职责不同，不能拿 proposal 的结构去要求 design/tasks。
REQUIRED_SECTIONS: dict[str, dict[str, str]] = {
    "proposal": {
        "背景与目标": "目标层（Why）",
        "功能范围": "范围层（What）",
        "验收标准": "验收层（Done）",
    },
    "design": {
        "系统架构": "架构方案",
        "模块职责": "模块边界（含负向边界）",
        "数据模型": "数据类型契约",
        "接口契约": "模块间的握手协议",
        "非功能性约束": "错误处理 / 安全 / 可运维性",
    },
    "tasks": {
        "任务清单": "任务清单本体",
    },
}


def _spec_kind(name: str) -> str:
    """从文件名判断规范类型。"""
    stem = Path(name).stem.lower()
    for kind in REQUIRED_SECTIONS:
        if kind in stem:
            return kind
    return "proposal"


def _rule_has_sections(text: str, target: str) -> list[ReviewFinding]:
    """结构完整性：按文档类型检查应含章节（第 4 章图 4-2 的三层结构只约束 proposal）。"""
    findings: list[ReviewFinding] = []
    kind = _spec_kind(target)

    # tasks.md 的结构由「任务可执行」规则按 Task 小节校验，这里只要求主标题存在
    if kind == "tasks":
        if not re.search(r"^#\s+\S", text, flags=re.MULTILINE):
            findings.append(
                ReviewFinding(
                    rule="结构完整", severity="error", message="tasks.md 缺少文档主标题"
                )
            )
        return findings

    for heading, layer in REQUIRED_SECTIONS[kind].items():
        if not re.search(rf"^#{{2,3}}\s*\d*\.?\s*{heading}", text, flags=re.MULTILINE):
            findings.append(
                ReviewFinding(
                    rule="结构完整",
                    severity="error",
                    message=f"缺少「{heading}」章节（应覆盖：{layer}）",
                )
            )
    return findings


def _rule_has_out_of_scope(text: str, target: str) -> list[ReviewFinding]:
    """范围有界：**只对 proposal.md 强制**"不做什么"（第 4 章图 4-3 关键点①）。"""
    if _spec_kind(target) != "proposal":
        return []
    if not re.search(r"不做什么|不做的|明确排除|非目标", text):
        return [
            ReviewFinding(
                rule="范围有界",
                severity="error",
                message='缺少"不做什么"的显式排除项 —— 不写红线，AI 会自行加功能',
            )
        ]
    block = re.search(r"###?\s*.*不做什么.*?\n(.*?)(?=\n##|\Z)", text, flags=re.DOTALL)
    if block and len(re.findall(r"^-\s+\S", block.group(1), flags=re.MULTILINE)) < 3:
        return [
            ReviewFinding(
                rule="范围有界",
                severity="warning",
                message='"不做什么"条目偏少（少于 3 条），建议补充排除项',
            )
        ]
    return []


def _rule_acceptance_testable(text: str, target: str) -> list[ReviewFinding]:
    """验收可测：**按文档类型**检查不同的可测性载体。

    - proposal.md：验收标准必须是 `- [ ]` Checklist
    - tasks.md：每个任务必须带验收标准，且同样可勾选
    - design.md：接口契约必须写明参数与返回（签名级可验证）
    """
    findings: list[ReviewFinding] = []
    kind = _spec_kind(target)
    vague = ["能用就行", "尽量", "大概", "比较快", "好看", "差不多", "非常", "尽量快"]

    if kind in ("proposal", "tasks"):
        if not re.search(r"^\s*-\s*\[[ x]\]", text, flags=re.MULTILINE):
            findings.append(
                ReviewFinding(
                    rule="验收可测",
                    severity="error",
                    message="验收标准不是可勾选的 Checklist（- [ ]），无法逐条转成测试用例",
                )
            )
    elif kind == "design":
        if not re.search(r"->|→", text):
            findings.append(
                ReviewFinding(
                    rule="验收可测",
                    severity="error",
                    message="接口契约没有给出函数签名（缺少 → / -> 形式的输入输出约定）",
                )
            )

    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip().startswith("-"):
            continue
        for word in vague:
            if word in line:
                findings.append(
                    ReviewFinding(
                        rule="验收可测",
                        severity="error",
                        message=f'含模糊表述"{word}"，必须可观测、可二值判定',
                        line=i,
                        evidence=line.strip(),
                    )
                )
    return findings


def _rule_performance_numeric(text: str, target: str) -> list[ReviewFinding]:
    """性能有数：**只对 proposal.md 强制**，因为可行性指标定在需求层。"""
    if _spec_kind(target) != "proposal":
        return []
    if not re.search(r"性能", text):
        return [
            ReviewFinding(rule="性能有数", severity="warning", message="规范中没有性能相关要求")
        ]
    block = re.search(r"###?\s*.*性能.*?\n(.*?)(?=\n##|\n###|\Z)", text, flags=re.DOTALL)
    if block and not re.search(r"\d", block.group(1)):
        return [
            ReviewFinding(
                rule="性能有数",
                severity="error",
                message="性能验收标准里没有任何数字 —— 没有数字的性能要求 = 没有性能要求",
            )
        ]
    return []


def _rule_boundary_cases(text: str, target: str) -> list[ReviewFinding]:
    """边界覆盖：proposal 要有边界场景；design 要有错误处理策略。"""
    kind = _spec_kind(target)
    if kind == "proposal" and not re.search(r"边界|异常|失败|超时|限流|重试", text):
        return [
            ReviewFinding(
                rule="边界覆盖",
                severity="error",
                message="没有边界/异常场景 —— 边界场景是 bug 的重灾区",
            )
        ]
    if kind == "design" and not re.search(r"错误处理|重试|降级|异常", text):
        return [
            ReviewFinding(
                rule="边界覆盖",
                severity="error",
                message="design.md 未定义错误处理策略（优雅降级）",
            )
        ]
    return []


def _rule_no_impl_details(text: str, target: str) -> list[ReviewFinding]:
    """无实现细节：**只对 proposal.md** 检查（第 4 章表 4-1：What 与 How 的边界）。"""
    if _spec_kind(target) != "proposal":
        return []
    patterns = [
        (r"import\s+\w+", "代码 import"),
        (r"def\s+\w+\s*\(", "函数定义"),
        (r"class\s+\w+\s*[\(:]", "类定义"),
        (r"SELECT\s+.+FROM", "SQL 语句"),
    ]
    findings: list[ReviewFinding] = []
    for i, line in enumerate(text.splitlines(), 1):
        for pattern, label in patterns:
            if re.search(pattern, line):
                findings.append(
                    ReviewFinding(
                        rule="无实现细节",
                        severity="warning",
                        message=f'proposal.md 出现了{label}，实现细节应放在 design.md（第 4 章表 4-1）',
                        line=i,
                        evidence=line.strip()[:80],
                    )
                )
    return findings


def _rule_term_consistency(text: str, _: str) -> list[ReviewFinding]:
    """术语一致：同一概念不能混用多种叫法。"""
    findings: list[ReviewFinding] = []
    pairs = [("日报", "报告"), ("成员", "员工"), ("仓库", "项目库")]
    for canonical, variant in pairs:
        if canonical in text and variant in text:
            findings.append(
                ReviewFinding(
                    rule="术语一致",
                    severity="warning",
                    message=f'"{canonical}" 与 "{variant}" 混用，应在术语表中统一',
                )
            )
    return findings


def _rule_task_structure(text: str, target: str) -> list[ReviewFinding]:
    """任务可执行：**只对 tasks.md** 检查（书图 6-2 的三层结构）。"""
    if _spec_kind(target) != "tasks":
        return []
    findings: list[ReviewFinding] = []
    task_count = len(re.findall(r"^##\s*Task\s*\d+", text, flags=re.MULTILINE))
    if task_count == 0:
        findings.append(
            ReviewFinding(rule="任务可执行", severity="error", message="没有解析到任何 Task 小节")
        )
        return findings

    for field_name, label in (("描述", "做什么"), ("输出", "产出什么"), ("依赖", "前置依赖")):
        n = len(re.findall(rf"^\s*{field_name}\s*[:：]", text, flags=re.MULTILINE))
        if n < task_count:
            findings.append(
                ReviewFinding(
                    rule="任务可执行",
                    severity="warning",
                    message=f"{task_count - n} 个任务缺少「{field_name}」字段（{label}）",
                )
            )
    return findings


RULES: list[tuple[str, Callable[[str, str], list[ReviewFinding]]]] = [
    ("结构完整", _rule_has_sections),
    ("范围有界", _rule_has_out_of_scope),
    ("验收可测", _rule_acceptance_testable),
    ("性能有数", _rule_performance_numeric),
    ("边界覆盖", _rule_boundary_cases),
    ("无实现细节", _rule_no_impl_details),
    ("术语一致", _rule_term_consistency),
    ("任务可执行", _rule_task_structure),
]


def _rule_term_consistency(text: str, _: str) -> list[ReviewFinding]:
    """术语一致：同一概念不能混用多种叫法。"""
    findings: list[ReviewFinding] = []
    # 本项目已定的术语对；出现其中一边却从未定义另一边才提醒
    pairs = [("日报", "报告"), ("成员", "员工"), ("仓库", "项目库")]
    for canonical, variant in pairs:
        if canonical in text and variant in text:
            findings.append(
                ReviewFinding(
                    rule="术语一致",
                    severity="warning",
                    message=f'"{canonical}" 与 "{variant}" 混用，应在术语表中统一',
                )
            )
    return findings


RULES: list[tuple[str, Callable[[str, str], list[ReviewFinding]]]] = [
    ("目标明确", _rule_has_sections),
    ("范围有界", _rule_has_out_of_scope),
    ("验收可测", _rule_acceptance_testable),
    ("性能有数", _rule_performance_numeric),
    ("边界覆盖", _rule_boundary_cases),
    ("无实现细节", _rule_no_impl_details),
    ("术语一致", _rule_term_consistency),
]


def deterministic_reviewer(text: str, target: str) -> list[ReviewFinding]:
    """默认评审者：按第 4 章检查清单做确定性检查（无需 LLM）。"""
    findings: list[ReviewFinding] = []
    for _name, rule in RULES:
        findings.extend(rule(text, target))
    return findings


def llm_reviewer_stub(text: str, target: str) -> list[ReviewFinding]:
    """LLM 评审者的接口占位。

    接入真实模型时，把检查清单与规范全文作为提示词发给模型，要求它按
    `ReviewFinding` 的结构化格式返回。此处保留签名以便替换，
    并明确抛出异常而不是静默回退 —— 静默回退会让调用方误以为经过了 LLM 审查。
    """
    raise NotImplementedError(
        "尚未接入 LLM 评审者。请实现本函数，返回 list[ReviewFinding]；"
        "或继续使用默认的确定性评审者。"
    )


def review_spec(
    path: str | Path,
    *,
    reviewer: Callable[[str, str], list[ReviewFinding]] | None = None,
) -> ReviewResult:
    """对一个规范文件执行生成—评审模式的"评审"环节。"""
    spec_path = Path(path)
    if not spec_path.exists():
        raise FileNotFoundError(f"待评审的规范不存在：{spec_path}")
    text = spec_path.read_text(encoding="utf-8")
    return review_spec_from_text(text, spec_path.name, reviewer=reviewer)


def review_spec_from_text(
    text: str,
    target_name: str,
    *,
    reviewer: Callable[[str, str], list[ReviewFinding]] | None = None,
) -> ReviewResult:
    """评审一段规范文本（不落盘）。

    :param target_name: 用于判定文档类型（proposal / design / tasks），
        因为检查项按文档类型区分。
    """
    active = reviewer or deterministic_reviewer
    return ReviewResult(target=target_name, findings=list(active(text, target_name)))


def review_many(paths: Iterable[str | Path], **kwargs) -> list[ReviewResult]:
    return [review_spec(p, **kwargs) for p in paths]
