"""逆向工程提示词（第 7.4.3 节原文照抄）。

书中印刷 p.141 给出的 Prompt 示例：

    请阅读 collector/github.py 的代码，并提取以下信息：
    1. 对外接口（函数签名及返回值）
    2. 依赖的外部服务或模块
    3. 异常处理机制
    4. 行为边界（不同输入下的预期行为）

    输出要求：请严格按照 design.md 的接口契约格式输出

本模块把这段 Prompt 参数化，便于对不同模块复用，并保留原文结构。
"""

from __future__ import annotations

from pathlib import Path

# 书中原文的四项提取要求
EXTRACTION_ITEMS = [
    "对外接口（函数签名及返回值）",
    "依赖的外部服务或模块",
    "异常处理机制",
    "行为边界（不同输入下的预期行为）",
]

OUTPUT_REQUIREMENT = "请严格按照 design.md 的接口契约格式输出"


def build_reverse_engineering_prompt(module_path: str | Path) -> str:
    """生成书中那段逆向工程 Prompt（四项要求 + 输出格式要求）。"""
    numbered = "\n".join(
        f"{i}. {item}" for i, item in enumerate(EXTRACTION_ITEMS, start=1)
    )
    return (
        f"请阅读 {module_path} 的代码，并提取以下信息：\n"
        f"{numbered}\n\n"
        f"输出要求：{OUTPUT_REQUIREMENT}"
    )


def build_human_review_prompt(module_path: str | Path) -> str:
    """第二步"规范化"的人工审查提示。

    书中强调这一步**必须人工介入**，因为"代码中的现有行为并不等同于正确行为"。
    这段提示的作用是引导人逐条判断，而不是把判断权交给 AI。
    """
    return (
        f"下面是为 {module_path} 生成的接口契约草稿。请逐条审查：\n\n"
        "对每一条隐性决策，判断属于以下哪一种，并写明理由：\n"
        "  A. 保留 —— 理由充分，作为契约固化下来\n"
        "  B. 修正 —— 现有行为不正确，需要改（写清楚改成什么）\n"
        "  C. 标记为历史遗留 —— 暂时保留但要记入技术债\n\n"
        "审查时特别注意：\n"
        "  1. 有没有遗漏的异常处理？\n"
        "  2. 有没有历史遗留的临时方案（hack）应当删除？\n"
        "  3. 魔法数字的真实含义是什么？依据是什么？\n"
        "  4. 是否存在职责越界（一个模块做了别的模块的事）？\n"
        "  5. 静默兜底（如 get(key, 0)）能否区分'缺失'与'真为 0'？\n"
    )


def build_test_prompt(module_path: str | Path) -> str:
    """第三步"补写测试"的提示。

    书中要点：这些测试不仅用于验证现有行为，更能暴露代码中未覆盖的边界情况。
    """
    return (
        f"请针对 {module_path} 的接口契约生成测试用例，要求：\n"
        "1. 每条契约的每个行为边界至少一个用例\n"
        "2. 覆盖异常路径（超时、认证失败、限流、空输入）\n"
        "3. 对契约中标注为'修正'的项，测试应断言**修正后**的行为，\n"
        "   并额外补一个特征化测试记录修正前的行为，便于回归对比\n"
        "4. 不依赖真实网络与真实 SMTP，全部使用替身\n"
    )
