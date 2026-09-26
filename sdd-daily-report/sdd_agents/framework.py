"""SDD 框架选型决策树（第 3 章 3.1 / 图 3-7）。

书中图 3-7「SDD 工具选择决策树」是三个判定节点：

    Q1：个人项目还是团队协作？
        └ 个人 → 结论：specs/ 目录 + Markdown 即可，甚至不需要框架
    Q2：团队规模是多少？
        ├ 5 人以上 → 结论：考虑 BMAD 等支持多角色编排的框架
        └ 2~5 人 → Q3
    Q3：是否需要 CI/CD 自动化？
        ├ 是 → 结论：推荐 Spec-Kit（支持 CI/CD 自动化）
        └ 否 → 结论：推荐 OpenSpec（提供文档模板）

本模块把该决策树实现为可执行函数，并保留书中的判定路径以便解释结论由来。
配套说明见 `docs/SDD开源框架对照.md`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Framework(str, Enum):
    """候选方案。前四项对应书中表 3-1 的框架，最后一项是书中推荐的"无框架"起点。"""

    NONE = "无需框架：specs/ 目录 + Markdown"
    OPENSPEC = "OpenSpec（文档驱动，工具无关）"
    SPEC_KIT = "Spec-Kit（CLI 驱动，治理与自动化优先）"
    BMAD = "BMAD（角色驱动，多智能体编排）"
    SUPERPOWERS = "Superpowers（TDD 派，强调工程纪律）"


@dataclass
class Selection:
    """选型结果。"""

    framework: Framework
    path: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "SDD 框架选型结果",
            "=" * 62,
            f"  推荐：{self.framework.value}",
            "=" * 62,
            "  判定路径：",
        ]
        lines += [f"    {i}. {step}" for i, step in enumerate(self.path, 1)]
        if self.reasons:
            lines.append("  依据：")
            lines += [f"    - {r}" for r in self.reasons]
        if self.caveats:
            lines.append("  注意事项：")
            lines += [f"    - {c}" for c in self.caveats]
        return "\n".join(lines)


def recommend(
    *,
    is_team: bool,
    team_size: int | None = None,
    needs_cicd: bool | None = None,
) -> Selection:
    """按书图 3-7 的决策树给出推荐。

    :param is_team: Q1 —— 是团队协作还是个人项目
    :param team_size: Q2 —— 仅当 is_team 为真时需要
    :param needs_cicd: Q3 —— 仅当 2 <= team_size <= 5 时需要
    """
    path: list[str] = ["Q1 个人项目还是团队协作？"]

    # ---- Q1：个人 ----
    if not is_team:
        path.append("→ 个人项目")
        return Selection(
            framework=Framework.NONE,
            path=path,
            reasons=[
                "书中印刷 p.50：一个人开发独立项目，OpenSpec 的 Markdown 模板就够了",
                "书中印刷 p.64：最简方案是建 specs/ 目录 + Markdown，再把 CLAUDE.md 指向它",
            ],
            caveats=[
                "如果坚持 TDD 纪律，可考虑 Superpowers 的原子技能（表 3-1 列为 1~5 人、TDD 派）",
                "书中提醒：不要在工具选择上耗费过多精力，先写出最粗糙的 proposal.md",
            ],
        )

    # ---- Q2：团队规模 ----
    path.append("→ 团队协作")
    path.append("Q2 团队规模是多少？")

    if team_size is None:
        raise ValueError("团队协作场景需要提供 team_size")
    if team_size < 1:
        raise ValueError("team_size 必须为正整数")

    if team_size > 5:
        path.append(f"→ {team_size} 人（> 5 人）")
        return Selection(
            framework=Framework.BMAD,
            path=path,
            reasons=[
                "书图 3-7：5 人以上 → 考虑 BMAD 等支持多角色编排的框架",
                "表 3-1：BMAD 适合 3~10 人的复杂项目与多 Agent 协作",
            ],
            caveats=[
                "学习成本高（12 种角色、34 种工作流），需评估团队接受度",
                "存量系统支持程度为「中等」，Brownfield 项目需额外评估",
            ],
        )

    # ---- Q3：是否需要 CI/CD 自动化 ----
    path.append(f"→ {team_size} 人（2~5 人）")
    path.append("Q3 是否需要 CI/CD 自动化？")

    if needs_cicd is None:
        raise ValueError("2~5 人场景需要提供 needs_cicd")
    if needs_cicd:
        path.append("→ 需要")
        return Selection(
            framework=Framework.SPEC_KIT,
            path=path,
            reasons=[
                "书图 3-7：2~5 人且需要 CI/CD → 推荐 Spec-Kit（支持 CI/CD 自动化）",
                "表 3-1：Spec-Kit 由 GitHub 推出，规范即主工件、治理优先，适合 1~10 人的企业级项目",
            ],
            caveats=[
                "书中的评价：从 Constitution 到 Implement 的完整流程对个人或小项目可能过于厚重",
                "若只需要模板而不需要治理门禁，OpenSpec 更轻",
            ],
        )

    path.append("→ 不需要")
    return Selection(
        framework=Framework.OPENSPEC,
        path=path,
        reasons=[
            "书图 3-7：2~5 人且不需要 CI/CD → 推荐 OpenSpec（提供文档模板）",
            "表 3-1：OpenSpec 轻量迭代、工具无关，兼容 20 余种 AI 工具",
        ],
        caveats=[
            "OpenSpec 文档自称是「轻量替代 Spec-Kit 的方案」，治理能力较弱",
            "存量系统支持为「强（Brownfield-first）」，适合边实现边补规范",
        ],
    )


# 决策树结构（供渲染与文档核对）
DECISION_TREE = {
    "Q1": {
        "question": "个人项目还是团队协作？",
        "branches": {
            "个人": "无框架：specs/ + Markdown",
            "团队": "Q2",
        },
    },
    "Q2": {
        "question": "团队规模是多少？",
        "branches": {
            "5 人以上": "BMAD",
            "2~5 人": "Q3",
        },
    },
    "Q3": {
        "question": "是否需要 CI/CD 自动化？",
        "branches": {
            "是": "Spec-Kit",
            "否": "OpenSpec",
        },
    },
}


def render_decision_tree() -> str:
    """渲染决策树（书中图 3-7 的文字版）。"""
    lines = ["SDD 工具选择决策树（第 3 章 图 3-7）", "=" * 62]
    for node, spec in DECISION_TREE.items():
        lines.append(f"\n  {node}：{spec['question']}")
        for condition, outcome in spec["branches"].items():
            lines.append(f"      {condition:<12} → {outcome}")
    lines += [
        "",
        "=" * 62,
        "  书中结论（印刷 p.50）：",
        "    「没有银弹。……关键不在于你使用什么工具，",
        "      而在于你有没有在编写代码前先撰写规范。",
        "      工具是加速器，不是方法论本身。」",
    ]
    return "\n".join(lines)
