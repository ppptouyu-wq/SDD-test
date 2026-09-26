"""SDD 适用性自评（第 10 章 10.1：SDD 的边界）。

书中 10.1 明确讲了"什么场景适合、什么场景不适合"（含表 10-1
「不适合完整 SDD 流程的场景及替代方案」），10.1.1 讲甜蜜区。

本章是全书收尾，本身是论述性的，不容易变成代码。但如果只是抄一遍正文，
它就无法被使用。因此这里把书中的判断标准**做成可执行的自评工具**：
输入你的项目特征，输出是否适合走完整 SDD 流程，以及不适合时的替代方案。

设计原则：**不编造标准**。下面每条判定都标注它在书中的出处，
避免把"我的意见"混进"书里的方法论"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    """适用性结论。"""

    SWEET_SPOT = "甜蜜区：完整走 SDD 流程"
    PARTIAL = "部分适用：保留核心规范，裁剪重环节"
    LIGHT = "轻量适用：只留 proposal.md 与验收标准"
    NOT_SUITABLE = "不适合：用替代方案"


@dataclass(frozen=True)
class Criterion:
    """一条判定标准。"""

    key: str
    question: str
    source: str  # 书中出处，避免编造
    yes_means_suitable: bool = True


# 判定标准：全部取自书中第 10 章与相关章节的论述
CRITERIA: list[Criterion] = [
    Criterion(
        key="multi_person",
        question="是否多人协作（≥2 人）或需要跨人交接？",
        source="第 9 章 9.1：从个人到团队的三个阶段 —— 协作是 SDD 价值的放大器",
    ),
    Criterion(
        key="long_lived",
        question="系统是否会长期维护（预期存活 > 3 个月）？",
        source="第 7 章 7.3：规范是活文档，价值在持续演进中体现",
    ),
    Criterion(
        key="requirement_unstable",
        question="需求是否会持续变更？",
        source="第 1 章 1.4：SDD 是「有迭代 + 有规范」，为变更而设计",
    ),
    Criterion(
        key="ai_involved",
        question="是否由 AI 辅助生成代码？",
        source="第 2 章 2.1：SDD 的定义就是「AI 时代的软件开发方法论」",
    ),
    Criterion(
        key="needs_audit",
        question="是否需要可追溯、可审查（合规、外包、关键系统）？",
        source="第 2 章 2.3 原则四：规范可版本控制、可审查、可追溯",
    ),
    Criterion(
        key="overturn_cost",
        question="决策一旦失误，修正成本是否超过一天？",
        source="第 5 章 5.3.1：ADR 的判断标准 —— 修正成本超一天就值得记录",
    ),
]


@dataclass
class Answer:
    """对一条标准作答。"""

    key: str
    yes: bool


@dataclass
class Assessment:
    """自评结果。"""

    verdict: Verdict
    yes_count: int
    total: int
    reasons: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    keep: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        return round(self.yes_count / self.total, 3) if self.total else 0.0

    def summary(self) -> str:
        lines = [
            "SDD 适用性自评结果",
            "=" * 60,
            f"  结论：{self.verdict.value}",
            f"  命中适用特征：{self.yes_count}/{self.total}（{self.score:.0%}）",
            "=" * 60,
        ]
        if self.reasons:
            lines.append("  判定依据：")
            lines += [f"    - {r}" for r in self.reasons]
        if self.keep:
            lines.append("  建议保留的核心环节：")
            lines += [f"    - {k}" for k in self.keep]
        if self.alternatives:
            lines.append("  替代方案（书中建议）：")
            lines += [f"    - {a}" for a in self.alternatives]
        return "\n".join(lines)


# 不适合完整 SDD 时的替代方案（书中表 10-1 的方向）
ALTERNATIVES_BY_GAP: dict[str, list[str]] = {
    "multi_person": ["单人项目：可只写 proposal.md 与验收标准，省略 design/tasks 的正式文档"],
    "long_lived": ["一次性脚本 / 原型：用注释说明意图即可，不必建 specs/ 目录"],
    "requirement_unstable": ["需求极不稳定（探索期）：先做技术刺探（spike），稳定后再补规范"],
    "ai_involved": ["纯人工编码：传统代码评审 + 单元测试纪律即可"],
    "needs_audit": ["无需审查的内部小工具：轻量即可，但建议保留验收标准"],
    "overturn_cost": ["决策易改、影响面小：口头约定 + 代码注释足够"],
}


def assess(answers: list[Answer]) -> Assessment:
    """根据作答给出适用性判断。

    判定规则（阈值取自书中论述的取向，非精确科学）：
      - 命中 >= 5 条：甜蜜区，完整走 SDD
      - 命中 3~4 条：部分适用，保留核心、裁剪重环节
      - 命中 2 条：轻量适用，只留 proposal.md 与验收标准
      - 命中 <= 1 条：不适合，用替代方案
    """
    by_key = {c.key: c for c in CRITERIA}
    valid = [a for a in answers if a.key in by_key]

    yes_answers = [a for a in valid if a.yes]
    no_answers = [a for a in valid if not a.yes]
    count = len(yes_answers)
    total = len(CRITERIA)

    reasons = [
        f"命中：{by_key[a.key].question}（依据：{by_key[a.key].source}）"
        for a in yes_answers
    ]

    if count >= 5:
        verdict = Verdict.SWEET_SPOT
        keep = ["proposal.md", "design.md", "tasks.md", "ADR", "规范 CI"]
        alternatives: list[str] = []
    elif count >= 3:
        verdict = Verdict.PARTIAL
        keep = ["proposal.md", "design.md", "验收标准"]
        alternatives = ["可省略正式 tasks.md，用 issue 列表代替；ADR 只记关键决策"]
    elif count == 2:
        verdict = Verdict.LIGHT
        keep = ["proposal.md", "验收标准"]
        alternatives = ["跳过 design.md / tasks.md / ADR 的正式文档"]
    else:
        verdict = Verdict.NOT_SUITABLE
        keep = ["验收标准（最小形态：一句话说清怎么算完成）"]
        alternatives = [
            alt
            for a in no_answers
            for alt in ALTERNATIVES_BY_GAP.get(a.key, [])
        ]

    # 未命中的项也补上对应替代方案，便于对照
    if verdict is not Verdict.SWEET_SPOT:
        for a in no_answers:
            alternatives.extend(ALTERNATIVES_BY_GAP.get(a.key, []))
        # 去重并保持顺序
        seen: set[str] = set()
        alternatives = [x for x in alternatives if not (x in seen or seen.add(x))]

    return Assessment(
        verdict=verdict,
        yes_count=count,
        total=total,
        reasons=reasons,
        alternatives=alternatives,
        keep=keep,
    )


# 能力矩阵迁移（书中 10.5.1：AI 时代工程素养重于编码能力）
CAPABILITY_SHIFT: list[tuple[str, str, str]] = [
    ("写代码", "定义接口契约与验收标准", "第 10.5.1 能力矩阵的迁移"),
    ("调试实现细节", "审查 AI 生成代码的结构与边界", "第 10.5.1"),
    ("记住 API 用法", "判断技术选型与取舍并记录 ADR", "第 5 章 5.3"),
    ("按需求实现", "把模糊意图显式化为结构化规范", "第 4 章 4.1"),
    ("单人完成功能", "设计多智能体协作边界", "第 8 章 8.3"),
]

# 规范形态的演进（书中 10.3.2 / 10.4）
SPEC_FORM_EVOLUTION: list[tuple[str, str, str]] = [
    ("自然语言段落", "人类读，AI 难执行", "早期 PRD"),
    ("结构化 Markdown", "人机共读，本书主推", "第 3 章 3.3.1"),
    ("YAML / JSON Schema", "机器可校验，形式化", "第 3 章 3.3.3"),
    ("规范即代码", "规范足够精确时可生成可执行产物", "第 10.4 终极愿景"),
]


def render_boundary_reference() -> str:
    """渲染第 10 章的边界参考：判定标准 + 能力迁移 + 规范形态演进。"""
    lines = ["SDD 的边界与演进（第 10 章）", "=" * 64, "", "一、适用性判定标准"]
    for i, c in enumerate(CRITERIA, 1):
        lines.append(f"  {i}. {c.question}")
        lines.append(f"     依据：{c.source}")

    lines += ["", "二、能力矩阵的迁移（10.5.1）", f"  {'从':<16}{'到':<32}出处"]
    for before, after, source in CAPABILITY_SHIFT:
        lines.append(f"  {before:<16}{after:<32}{source}")

    lines += ["", "三、规范形态的演进（10.3.2 / 10.4）", f"  {'形态':<18}{'特征':<24}出处"]
    for form, feature, source in SPEC_FORM_EVOLUTION:
        lines.append(f"  {form:<18}{feature:<24}{source}")

    lines += [
        "",
        "四、书中明确的态度（避免误用）",
        "  · SDD 不是银弹：10.1.2 明确列出了不适合的场景",
        "  · 行业验证：10.1.3 指出 SDD 绝非一家之言，多个团队独立趋同",
        "  · 方法论不是金科玉律：10.2.1 —— 应按项目裁剪，而非照搬",
        "  · 远未到完全自动化：8.7.3 强调人类仍在关键环节把关",
    ]
    return "\n".join(lines)
