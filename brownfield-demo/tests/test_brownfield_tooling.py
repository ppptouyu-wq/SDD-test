"""测试：Brownfield 逆向工程分析器与提示词。

分析器是第 7.4.3 节第一步的工具，必须能稳定地从代码中提取出
"隐性决策"与"边界待确认项" —— 否则第二步的人工审查就没有输入。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.brownfield import (  # noqa: E402
    analyze_module,
    analyze_tree,
    render_interface_contract,
)
from tools.prompts import (  # noqa: E402
    EXTRACTION_ITEMS,
    build_human_review_prompt,
    build_reverse_engineering_prompt,
    build_test_prompt,
)

LEGACY = ROOT / "legacy"
GITHUB = LEGACY / "collector" / "github.py"
BUILDER = LEGACY / "report" / "builder.py"


# ------------------------------------------------------- 第一步：逆向工程

def test_analyze_extracts_public_interface():
    analysis = analyze_module(GITHUB)
    assert "GithubCollector.collect" in analysis.public_functions
    assert "GithubCollector.check_token" in analysis.public_functions


def test_analyze_extracts_external_dependencies():
    analysis = analyze_module(GITHUB)
    assert "urllib.request" in analysis.imports
    assert "json" in analysis.imports


def test_analyze_finds_magic_numbers_as_implicit_decisions():
    """重试 3 次、间隔 5s、超时 30s 这些魔法数字必须被识别。"""
    analysis = analyze_module(GITHUB)
    joined = " ".join(analysis.implicit_decisions)
    assert "字面量 3" in joined
    assert "字面量 5" in joined
    assert "字面量 30" in joined


def test_analyze_finds_implicit_sorting():
    """sorted() 是隐性保证的输出顺序，必须被标出。"""
    analysis = analyze_module(BUILDER)
    assert any("sorted()" in d for d in analysis.implicit_decisions)


def test_analyze_finds_silent_zero_fallback():
    """dict.get(key, 0) 会让'缺失'与'真为 0'无法区分，必须被标出。"""
    analysis = analyze_module(GITHUB)
    assert any("静默填 0" in d for d in analysis.implicit_decisions)


def test_analyze_flags_bare_except():
    analysis = analyze_module(GITHUB)
    assert any("except" in flag for flag in analysis.boundary_flags)


def test_analyze_flags_blocking_sleep():
    analysis = analyze_module(GITHUB)
    assert any("sleep" in flag for flag in analysis.boundary_flags)


def test_analyze_reports_undocumented_members():
    analysis = analyze_module(GITHUB)
    assert "GithubCollector.collect" in analysis.undocumented


def test_analyze_tree_skips_pycache():
    paths = [a.path for a in analyze_tree(LEGACY)]
    assert paths, "应至少分析到一个模块"
    assert not any("__pycache__" in p for p in paths)


def test_analyze_does_not_invent_number_meaning():
    """分析器不猜魔法数字的含义 —— 猜错会误导人工审查。

    因此决策描述里必须给出源码行内容，而不是编造一个用途。
    """
    analysis = analyze_module(GITHUB)
    decisions = [d for d in analysis.implicit_decisions if "字面量 3" in d]
    assert decisions
    assert "源码为" in decisions[0]


# --------------------------------------------- 第二步：契约草稿

def test_render_contract_contains_interface_table():
    analysis = analyze_module(GITHUB)
    text = render_interface_contract(analysis, module_name="github")

    assert "## github" in text
    assert "| 函数 |" in text
    assert "`collect`" in text or "`GithubCollector.collect`" in text


def test_render_contract_marks_items_for_human_judgement():
    """契约草稿必须明确标注"需要人工判断"，不能像定稿一样呈现。"""
    analysis = analyze_module(GITHUB)
    text = render_interface_contract(analysis, module_name="github")

    assert "必须经人工审查" in text
    assert "人工判断：保留 / 修正 / 标记为遗留" in text


def test_render_contract_lists_dependencies():
    analysis = analyze_module(GITHUB)
    text = render_interface_contract(analysis, module_name="github")
    assert "### 外部依赖" in text
    assert "`urllib.request`" in text


# --------------------------------------------------- 提示词

def test_reverse_engineering_prompt_matches_book():
    """书中印刷 p.141 的四项要求与输出格式要求，逐条对照。"""
    prompt = build_reverse_engineering_prompt("collector/github.py")

    assert "collector/github.py" in prompt
    for item in EXTRACTION_ITEMS:
        assert item in prompt
    assert "请严格按照 design.md 的接口契约格式输出" in prompt


def test_extraction_items_are_the_four_from_the_book():
    assert EXTRACTION_ITEMS == [
        "对外接口（函数签名及返回值）",
        "依赖的外部服务或模块",
        "异常处理机制",
        "行为边界（不同输入下的预期行为）",
    ]


def test_human_review_prompt_emphasizes_human_judgement():
    """书中强调第二步必须人工介入：现有行为不等于正确行为。"""
    prompt = build_human_review_prompt("x.py")
    assert "保留" in prompt and "修正" in prompt and "历史遗留" in prompt
    assert "现有行为" in prompt


def test_test_prompt_requires_characterization_tests():
    prompt = build_test_prompt("x.py")
    assert "特征化测试" in prompt
    assert "替身" in prompt  # 不依赖真实网络/SMTP


# --------------------------------------------- 契约与实现的一致性

def test_baseline_contract_exists_and_is_adopted():
    """第四步要求：契约定稿后与测试一同提交，作为基线。"""
    spec = ROOT / "specs" / "contracts" / "legacy-baseline.md"
    assert spec.exists()
    text = spec.read_text(encoding="utf-8")
    assert "已采纳" in text
    assert "锁定基线" in text


def test_baseline_contract_decisions_are_reflected_in_fixed_code():
    """契约里判定为"修正"的项，必须在修正版实现中真的改掉。"""
    from legacy_fixed.collector import github as fixed_github
    from legacy_fixed.report import builder as fixed_report

    # 契约 1.2-#1/#2/#4：魔法数字成为具名常量
    assert fixed_github.MAX_RETRIES == 3
    assert fixed_github.RETRY_INTERVAL == 5.0
    assert fixed_github.TIMEOUT == 30.0
    # 契约 1.2-#3：限流等待有上限
    assert fixed_github.MAX_RATE_LIMIT_WAIT == 300.0
    # 契约 2.2-#2/#3：具名常量
    assert fixed_report.MESSAGE_MAX_LEN == 60
    assert fixed_report.MAX_ITEMS_PER_MEMBER == 200
