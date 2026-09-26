"""测试：SDD 框架选型决策树（第 3 章 3.1 / 图 3-7）。

决策树的每个分支都必须与书中一致，且结论要能解释由来。
这一节的产物是"生态认知"，测试的作用是保证决策树没被改错。
"""

from __future__ import annotations

import pytest

from sdd_agents.framework import (
    DECISION_TREE,
    Framework,
    recommend,
    render_decision_tree,
)


# ------------------------------------------------- 书图 3-7 的分支结构

def test_decision_tree_matches_book_structure():
    """三个判定节点与分支必须与书中图 3-7 一致。"""
    assert set(DECISION_TREE) == {"Q1", "Q2", "Q3"}
    assert DECISION_TREE["Q1"]["branches"]["个人"] == "无框架：specs/ + Markdown"
    assert DECISION_TREE["Q1"]["branches"]["团队"] == "Q2"
    assert DECISION_TREE["Q2"]["branches"]["5 人以上"] == "BMAD"
    assert DECISION_TREE["Q2"]["branches"]["2~5 人"] == "Q3"
    assert DECISION_TREE["Q3"]["branches"]["是"] == "Spec-Kit"
    assert DECISION_TREE["Q3"]["branches"]["否"] == "OpenSpec"


# --------------------------------------------------------- 四档结论

def test_solo_recommends_no_framework():
    """书：一个人开发独立项目，specs/ + Markdown 就够。"""
    result = recommend(is_team=False)

    assert result.framework is Framework.NONE
    assert any("个人项目" in step for step in result.path)
    # 结论要带出处
    assert any("印刷 p.50" in r or "印刷 p.64" in r for r in result.reasons)


def test_large_team_recommends_bmad():
    result = recommend(is_team=True, team_size=8)

    assert result.framework is Framework.BMAD
    assert any("8 人" in step for step in result.path)
    assert any("BMAD" in r for r in result.reasons)


def test_small_team_with_cicd_recommends_spec_kit():
    result = recommend(is_team=True, team_size=3, needs_cicd=True)

    assert result.framework is Framework.SPEC_KIT
    assert any("需要" == step.split("→ ")[-1] for step in result.path)


def test_small_team_without_cicd_recommends_openspec():
    result = recommend(is_team=True, team_size=3, needs_cicd=False)

    assert result.framework is Framework.OPENSPEC
    assert any("不需要" in step for step in result.path)


@pytest.mark.parametrize("size", [6, 10, 50])
def test_boundary_above_five_is_bmad(size):
    """> 5 人走 BMAD 分支。"""
    assert recommend(is_team=True, team_size=size).framework is Framework.BMAD


@pytest.mark.parametrize("size", [2, 5])
def test_boundary_two_to_five_goes_to_q3(size):
    """2~5 人需要回答 Q3，结论取决于 CI/CD。"""
    assert recommend(is_team=True, team_size=size, needs_cicd=True).framework is (
        Framework.SPEC_KIT
    )
    assert recommend(is_team=True, team_size=size, needs_cicd=False).framework is (
        Framework.OPENSPEC
    )


# ----------------------------------------------------------- 参数校验

def test_team_without_size_raises():
    with pytest.raises(ValueError, match="team_size"):
        recommend(is_team=True)


def test_small_team_without_cicd_answer_raises():
    with pytest.raises(ValueError, match="needs_cicd"):
        recommend(is_team=True, team_size=3)


def test_invalid_team_size_raises():
    with pytest.raises(ValueError):
        recommend(is_team=True, team_size=0)


# --------------------------------------------------------------- 输出

def test_summary_shows_path_and_caveats():
    text = recommend(is_team=True, team_size=3, needs_cicd=True).summary()

    assert "Spec-Kit" in text
    assert "判定路径" in text
    assert "注意事项" in text
    # 书中对 Spec-Kit 的负面评价也要传达，不能只讲优点
    assert "厚重" in text


def test_bmad_summary_warns_about_learning_cost():
    text = recommend(is_team=True, team_size=10).summary()
    assert "学习成本高" in text


def test_openspec_summary_mentions_brownfield_strength():
    text = recommend(is_team=True, team_size=3, needs_cicd=False).summary()
    assert "Brownfield" in text


def test_render_decision_tree_quotes_book_conclusion():
    text = render_decision_tree()

    assert "Q1" in text and "Q2" in text and "Q3" in text
    assert "没有银弹" in text
    assert "工具是加速器，不是方法论本身" in text


# ------------------------------------------------------------------ CLI

def test_cli_prints_tree_without_scenario(capsys):
    from sdd_agents.__main__ import main

    code = main(["framework"])
    out = capsys.readouterr().out

    assert code == 0
    assert "决策树" in out
    assert "BMAD" in out


def test_cli_solo(capsys):
    from sdd_agents.__main__ import main

    code = main(["framework", "--solo"])
    out = capsys.readouterr().out

    assert code == 0
    assert "无需框架" in out


def test_cli_team_with_size_and_cicd(capsys):
    from sdd_agents.__main__ import main

    code = main(["framework", "--team", "--size", "3", "--cicd", "yes"])
    out = capsys.readouterr().out

    assert code == 0
    assert "Spec-Kit" in out


def test_cli_missing_size_returns_2(capsys):
    from sdd_agents.__main__ import main

    code = main(["framework", "--team"])
    out = capsys.readouterr().out

    assert code == 2
    assert "team_size" in out
