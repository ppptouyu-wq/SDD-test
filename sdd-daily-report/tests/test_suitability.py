"""测试：SDD 适用性自评（第 10 章 10.1 — SDD 的边界）。

这一章在书里是论述性的，本复现把它做成可执行的自评工具。
测试要保证：判定标准都标注了书中出处（不编造）、四档结论边界正确、
不适合时能给出替代方案。
"""

from __future__ import annotations

from sdd_agents.suitability import (
    ALTERNATIVES_BY_GAP,
    CAPABILITY_SHIFT,
    CRITERIA,
    SPEC_FORM_EVOLUTION,
    Answer,
    Verdict,
    assess,
    render_boundary_reference,
)


def _answers(*yes_flags: bool) -> list[Answer]:
    """把布尔序列映射到 CRITERIA 的前 N 条，其余填 False。"""
    flags = list(yes_flags) + [False] * (len(CRITERIA) - len(yes_flags))
    return [Answer(key=c.key, yes=f) for c, f in zip(CRITERIA, flags)]


# ------------------------------------------------ 判定标准的可追溯性

def test_every_criterion_cites_a_source():
    """每条判定标准都必须标注书中出处 —— 不能把个人意见混进方法论。"""
    for criterion in CRITERIA:
        assert criterion.source.strip(), f"{criterion.key} 缺少出处"
        assert "第" in criterion.source and "章" in criterion.source, (
            f"{criterion.key} 的出处格式不对：{criterion.source}"
        )


def test_criteria_count_and_uniqueness():
    keys = [c.key for c in CRITERIA]
    assert len(keys) == len(set(keys)) >= 5


def test_alternatives_cover_every_gap():
    """每条标准都有对应的替代方案，否则判为"不适合"时会没话说。"""
    for criterion in CRITERIA:
        assert criterion.key in ALTERNATIVES_BY_GAP, (
            f"{criterion.key} 没有替代方案"
        )


# ------------------------------------------------------ 四档结论

def test_all_yes_is_sweet_spot():
    result = assess(_answers(True, True, True, True, True, True))

    assert result.verdict is Verdict.SWEET_SPOT
    assert result.yes_count == len(CRITERIA)
    assert result.score == 1.0
    assert result.alternatives == []
    assert "proposal.md" in result.keep


def test_five_yes_still_sweet_spot():
    result = assess(_answers(True, True, True, True, True))
    assert result.verdict is Verdict.SWEET_SPOT


def test_four_yes_is_partial():
    result = assess(_answers(True, True, True, True))
    assert result.verdict is Verdict.PARTIAL
    assert result.alternatives, "部分适用时应给出裁剪建议"


def test_three_yes_is_partial():
    assert assess(_answers(True, True, True)).verdict is Verdict.PARTIAL


def test_two_yes_is_light():
    result = assess(_answers(True, True))
    assert result.verdict is Verdict.LIGHT
    assert result.keep == ["proposal.md", "验收标准"]


def test_one_yes_is_not_suitable():
    assert assess(_answers(True)).verdict is Verdict.NOT_SUITABLE


def test_all_no_is_not_suitable_with_alternatives():
    result = assess(_answers())

    assert result.verdict is Verdict.NOT_SUITABLE
    assert result.score == 0.0
    # 六条都未命中 → 六条替代方案都应出现
    assert len(result.alternatives) == len(CRITERIA)
    assert any("一次性脚本" in a for a in result.alternatives)


def test_unknown_keys_are_ignored():
    """传入不存在的 key 不应影响判定。"""
    answers = _answers(True, True, True) + [Answer(key="不存在的标准", yes=True)]
    result = assess(answers)
    assert result.yes_count == 3
    assert result.verdict is Verdict.PARTIAL


# ------------------------------------------------------ 输出内容

def test_summary_contains_verdict_and_reasons():
    text = assess(_answers(True, True, True, True, True, True)).summary()

    assert "甜蜜区" in text
    assert "判定依据" in text
    assert "第 9 章" in text  # 出处被带出来


def test_summary_of_not_suitable_lists_alternatives():
    text = assess(_answers()).summary()

    assert "不适合" in text
    assert "替代方案" in text
    assert "技术刺探" in text  # 书中对需求不稳定场景的建议


def test_boundary_reference_covers_three_tables():
    text = render_boundary_reference()

    assert "适用性判定标准" in text
    assert "能力矩阵的迁移" in text
    assert "规范形态的演进" in text


def test_boundary_reference_warns_against_misuse():
    """第 10 章明确写了 SDD 不是银弹，这条态度必须传达出来。"""
    text = render_boundary_reference()

    assert "不是银弹" in text
    assert "金科玉律" in text  # 10.2.1：方法论应按项目裁剪
    assert "完全自动化" in text  # 8.7.3


def test_capability_shift_has_sources():
    for before, after, source in CAPABILITY_SHIFT:
        assert before and after
        assert "第" in source


def test_spec_form_evolution_is_ordered():
    """规范形态应体现从自然语言到形式化的递进。"""
    forms = [f for f, _feat, _src in SPEC_FORM_EVOLUTION]
    assert forms[0] == "自然语言段落"
    assert forms[-1] == "规范即代码"


def test_cli_prints_criteria_when_no_answers(capsys):
    """不带 --answers 时只打印标准与参考，不应报错。"""
    from sdd_agents.__main__ import main

    code = main(["suitability"])
    captured = capsys.readouterr()

    assert code == 0
    assert "适用性自评" in captured.out
    assert "是否多人协作" in captured.out


def test_cli_rejects_wrong_number_of_answers(capsys):
    from sdd_agents.__main__ import main

    code = main(["suitability", "--answers", "yes,no"])

    assert code == 2
    assert "需要" in capsys.readouterr().out


def test_cli_assesses_with_answers(capsys):
    from sdd_agents.__main__ import main

    code = main(["suitability", "--answers", "yes,yes,yes,yes,yes,yes"])

    assert code == 0
    assert "甜蜜区" in capsys.readouterr().out
