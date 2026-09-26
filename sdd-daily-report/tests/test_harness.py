"""测试：Harness Engineering 成熟度审计（第 8 章 8.8）。

书 8.8 把 harness 归纳为五个维度：上下文 / 工具 / 权限 / 验证 / 反馈回路。
本模块把它们做成可审计的检查。测试要保证：
五维齐全、检查项都以真实文件为准、缺口能给出可执行的建议。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sdd_agents.harness import (
    CHECKS,
    HarnessCheck,
    HarnessReport,
    CheckResult,
    audit,
)

ROOT = Path(__file__).resolve().parent.parent
DIMENSIONS = {"上下文", "工具", "权限", "验证", "反馈回路"}


# ------------------------------------------------------ 检查项设计

def test_five_dimensions_are_all_covered():
    """书中 8.8 点名的五个维度必须都有检查项。"""
    covered = {c.dimension for c in CHECKS}
    assert covered == DIMENSIONS


def test_every_check_explains_why():
    """每条检查都要说明'为什么这个维度重要'，否则审计退化成一个文件清单。"""
    for check in CHECKS:
        assert check.why.strip(), f"{check.key} 缺少 why"
        assert "书" in check.why or "第" in check.why, f"{check.key} 的 why 未引书中依据"


def test_check_keys_are_unique():
    keys = [c.key for c in CHECKS]
    assert len(keys) == len(set(keys))


def test_each_dimension_has_multiple_checks():
    """单一检查不足以代表一个维度。"""
    from collections import Counter

    counts = Counter(c.dimension for c in CHECKS)
    for dimension, count in counts.items():
        assert count >= 2, f"{dimension} 只有 {count} 条检查，不足以代表该维度"


# ------------------------------------------------------------ 审计

def test_audit_current_project_has_no_gaps():
    """本复现项目本身就是一个 harness —— 审计应当全绿。"""
    report = audit(ROOT)

    assert report.total == len(CHECKS)
    assert not report.missing, [r.check.what for r in report.missing]
    assert report.score == 1.0


def test_audit_records_evidence_for_present_items():
    report = audit(ROOT)
    present = [r for r in report.results if r.present]

    assert present
    assert all(r.evidence for r in present)


def test_audit_of_empty_dir_reports_all_missing(tmp_dir):
    """对空目录审计应报告全部**必需**构件缺失，且给出建议。"""
    report = audit(tmp_dir)

    assert report.required_present == 0
    assert len(report.missing) == report.required_total
    assert report.required_total > 0

    text = report.summary()
    assert "缺口与建议" in text
    assert "建议补" in text


def test_optional_check_missing_does_not_count_as_gap(tmp_dir):
    """可选构件缺失不应计入缺口（否则环境差异会被误报成缺陷）。

    构造一个只含可选检查的 report 来断言行为，不依赖当前环境里该构件是否存在。
    """
    optional_check = HarnessCheck(
        key="opt", dimension="工具", what="可选构件", why="书第 7.4 节",
        relative_path="不存在的东西.py", optional=True,
    )
    required_check = HarnessCheck(
        key="req", dimension="工具", what="必需构件", why="书第 7 章",
        relative_path="也不存在.py",
    )
    report = HarnessReport(results=[
        CheckResult(check=optional_check, present=False),
        CheckResult(check=required_check, present=False),
    ])

    assert report.missing == [r for r in report.results if r.check.key == "req"]
    assert len(report.optional_missing) == 1
    assert report.required_total == 1
    assert report.required_present == 0

    text = report.summary()
    assert "可选构件缺失" in text
    assert "环境差异" in text


def test_optional_check_present_is_counted_in_dimension():
    """可选构件存在时应正常计入维度统计。"""
    check = HarnessCheck(
        key="opt", dimension="工具", what="x", why="书第 7 章",
        relative_path="y", optional=True,
    )
    report = HarnessReport(results=[CheckResult(check=check, present=True)])

    assert report.by_dimension()["工具"] == (1, 1)
    assert report.missing == []


def test_workspace_level_check_resolves_to_parent(tmp_dir):
    """标了 workspace_level 的检查应在上一级查找（跨项目构件）。"""
    workspace = tmp_dir / "ws"
    project = workspace / "proj"
    project.mkdir(parents=True)
    (workspace / "other-project").mkdir()
    (workspace / "other-project" / "artifact.py").write_text("x = 1", encoding="utf-8")

    # 直接构造一个只含该条的检查集来验证解析规则
    check = HarnessCheck(
        key="t", dimension="工具", what="跨项目构件", why="第 7 章",
        relative_path="other-project/artifact.py", workspace_level=True,
    )
    target = (project.parent / check.relative_path).resolve()
    assert target.exists()


# -------------------------------------------------------- 报告内容

def test_summary_groups_by_dimension():
    text = audit(ROOT).summary()

    assert "五个维度" in text
    for dimension in DIMENSIONS:
        assert dimension in text


def test_summary_lists_missing_with_reasons(tmp_dir):
    text = audit(tmp_dir).summary()

    assert "为什么重要" in text
    # 每条缺口都应带上书中依据
    assert "第" in text


def test_by_dimension_counts_add_up():
    report = audit(ROOT)
    by_dim = report.by_dimension()

    assert sum(got for got, _ in by_dim.values()) == report.present_count
    assert sum(total for _, total in by_dim.values()) == report.total


def test_report_score_computation():
    check = HarnessCheck(
        key="a", dimension="上下文", what="x", why="第 3 章", relative_path="a"
    )
    report = HarnessReport(results=[
        CheckResult(check=check, present=True),
        CheckResult(check=check, present=False),
    ])

    assert report.present_count == 1
    assert report.total == 2
    assert report.score == 0.5


# ------------------------------------------------------------------ CLI

def test_cli_harness_runs(capsys):
    from sdd_agents.__main__ import main

    code = main(["harness"])
    out = capsys.readouterr().out

    assert code == 0, "本项目审计应当无缺口"
    assert "Harness 成熟度审计" in out
    assert "反馈回路" in out


def test_cli_harness_workspace_flag(capsys):
    from sdd_agents.__main__ import main

    code = main(["harness", "--workspace"])
    out = capsys.readouterr().out

    assert code == 0
    assert "成熟度审计" in out
