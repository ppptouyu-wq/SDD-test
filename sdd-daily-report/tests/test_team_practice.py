"""测试：团队实践与规范治理（第 9 章）、规范度量（第 9.5 节）。

覆盖：
  - 9.1 三阶段准入条件与失败信号
  - 9.2 三种角色的职责边界与职责分离
  - 9.3 模板三条底线（配合 team-templates/）
  - 9.5 规范度量：验收→测试映射覆盖度
  - 9.6 规范 CI 三层次 + 可自动化的治理检查
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sdd_agents import metrics as metrics_mod
from sdd_agents import workflow as wf

ROOT = Path(__file__).resolve().parent.parent


# ===================================================== 9.1 三阶段

def test_three_stages_exist():
    stages = [g.stage for g in wf.STAGE_GATES]
    assert stages == [wf.Stage.PILOT, wf.Stage.TEAM, wf.Stage.ORG]


def test_each_stage_has_requirements_and_failure_signal():
    """书 9.1.1：不能一步到位 —— 每个阶段都必须有准入条件。"""
    for gate in wf.STAGE_GATES:
        assert len(gate.requirements) >= 3, f"{gate.stage} 准入条件过少"
        assert gate.failure_signal, f"{gate.stage} 缺少失败信号（无法自检）"


def test_pilot_stage_emphasizes_choosing_right_people():
    """书 9.1.2：第一阶段的关键是选对试点人和试点项目。"""
    pilot = wf.STAGE_GATES[0]
    joined = " ".join(pilot.requirements)
    assert "试点人" in joined
    assert "试点项目" in joined


def test_next_stage_progression():
    assert wf.next_stage(wf.Stage.PILOT) is wf.Stage.TEAM
    assert wf.next_stage(wf.Stage.TEAM) is wf.Stage.ORG
    assert wf.next_stage(wf.Stage.ORG) is None


def test_stage_readiness_detects_missing_requirements():
    gate = wf.STAGE_GATES[0]
    # 只满足第一条
    report = wf.stage_readiness(wf.Stage.PILOT, [gate.requirements[0]])
    assert report["ready"] is False
    assert len(report["missing"]) == len(gate.requirements) - 1
    assert report["failure_signal"]


def test_stage_readiness_passes_when_all_satisfied():
    gate = wf.STAGE_GATES[1]
    report = wf.stage_readiness(wf.Stage.TEAM, list(gate.requirements))
    assert report["ready"] is True
    assert report["missing"] == []


def test_stage_readiness_rejects_unknown_stage():
    with pytest.raises(ValueError):
        wf.stage_readiness("不存在的阶段", [])  # type: ignore[arg-type]


# ===================================================== 9.2 三种角色

def test_three_roles_defined():
    assert set(wf.ROLE_DUTIES) == {wf.Role.AUTHOR, wf.Role.REVIEWER, wf.Role.EXECUTOR}


def test_each_role_has_duties_and_non_duties():
    """书 9.2.1：每种角色都要有"不负责"边界，否则会互相侵蚀。"""
    for role, duties in wf.ROLE_DUTIES.items():
        assert duties["职责"], f"{role} 无职责"
        assert duties["不负责"], f"{role} 缺少「不负责」边界"


def test_executor_must_fix_spec_not_patch_code():
    """书第 7 章核心：规范缺陷只能通过改规范来修，不能在代码里打补丁。"""
    duties = wf.ROLE_DUTIES[wf.Role.EXECUTOR]["职责"]
    assert any("回溯修改规范" in d or "规范缺陷" in d for d in duties)


def test_separation_of_duties_rejects_self_review():
    ok, reason = wf.separation_of_duties("张三", "张三")
    assert ok is False
    assert "自己" in reason


def test_separation_of_duties_accepts_different_people():
    ok, _ = wf.separation_of_duties("张三", "李四")
    assert ok is True


def test_separation_of_duties_requires_both_names():
    assert wf.separation_of_duties("", "李四")[0] is False
    assert wf.separation_of_duties("张三", "")[0] is False


# ===================================================== 9.6 规范 CI

def test_three_ci_layers_defined():
    assert len(wf.CI_LAYERS) == 3
    names = " ".join(layer.name for layer in wf.CI_LAYERS)
    assert "层次 1" in names and "层次 2" in names and "层次 3" in names


def test_ci_layer_report_mentions_workflow_file():
    text = wf.ci_layer_report()
    assert "spec-check.yml" in text


def test_spec_check_workflow_exists_and_has_three_layers():
    workflow_file = ROOT / ".github" / "workflows" / "spec-check.yml"
    assert workflow_file.exists()
    text = workflow_file.read_text(encoding="utf-8")
    assert "层次 1" in text and "层次 2" in text and "层次 3" in text


# ============================================== 治理检查（可自动化）

def test_adr_append_only_accepts_continuous_numbering(tmp_dir):
    adr_dir = tmp_dir / "adrs"
    adr_dir.mkdir()
    for i in (1, 2, 3):
        (adr_dir / f"{i:03d}-x.md").write_text("# ADR", encoding="utf-8")

    assert wf.check_adr_append_only(adr_dir) == []


def test_adr_append_only_detects_gap(tmp_dir):
    """删掉旧 ADR 会破坏"只追加"原则，必须报错。"""
    adr_dir = tmp_dir / "adrs"
    adr_dir.mkdir()
    for name in ("001-a.md", "003-c.md"):
        (adr_dir / name).write_text("# ADR", encoding="utf-8")

    findings = wf.check_adr_append_only(adr_dir)
    assert any(f.severity == "error" and "不连续" in f.message for f in findings)


def test_adr_append_only_detects_duplicate(tmp_dir):
    adr_dir = tmp_dir / "adrs"
    adr_dir.mkdir()
    for name in ("001-a.md", "001-b.md"):
        (adr_dir / name).write_text("# ADR", encoding="utf-8")

    findings = wf.check_adr_append_only(adr_dir)
    assert any("重复" in f.message for f in findings)


def test_real_project_adrs_pass_append_only():
    """本项目自己的 ADR 必须满足只追加原则。"""
    findings = wf.check_adr_append_only(ROOT / "specs" / "adrs")
    errors = [f for f in findings if f.severity == "error"]
    assert not errors, [str(f) for f in errors]


def test_template_baseline_detects_missing_out_of_scope(tmp_dir):
    spec = tmp_dir / "proposal.md"
    spec.write_text("# X\n\n## 3. 验收标准\n- [ ] 某条\n", encoding="utf-8")

    findings = wf.check_template_baseline(spec)
    assert any("不做什么" in f.message for f in findings)


def test_template_baseline_detects_missing_checklist(tmp_dir):
    spec = tmp_dir / "proposal.md"
    spec.write_text("# X\n\n### 2.2 不做什么\n- a\n- b\n- c\n", encoding="utf-8")

    findings = wf.check_template_baseline(spec)
    assert any("可勾选清单" in f.message for f in findings)


def test_real_project_specs_pass_template_baseline():
    findings = wf.run_governance_checks(ROOT)
    spec_errors = [
        f for f in findings.findings
        if f.rule == "模板底线" and f.severity == "error"
    ]
    assert not spec_errors, [str(f) for f in spec_errors]


def test_commit_scope_check_degrades_gracefully_without_git(tmp_dir):
    """不在 git 仓库里时应降级为 warning，不能阻断。"""
    findings = wf.check_commit_scope(tmp_dir)
    assert all(f.severity == "warning" for f in findings) or findings == []


def test_governance_report_passed_only_without_errors():
    report = wf.GovernanceReport()
    assert report.passed is True

    report.findings.append(wf.GovernanceFinding("r", "warning", "提醒"))
    assert report.passed is True

    report.findings.append(wf.GovernanceFinding("r", "error", "错误"))
    assert report.passed is False


# ================================================ 9.5 规范度量

def test_measure_spec_counts_acceptance_items():
    spec = metrics_mod.measure_spec(ROOT / "specs" / "proposal.md")
    assert spec.acceptance_items == 16  # 功能 9(含 v1.1 两条) + 性能 3 + 边界 4
    assert spec.out_of_scope_items == 5
    assert spec.lines > 0


def test_measure_spec_does_not_use_checkbox_rate_as_progress():
    """`- [ ]` 是规范格式（原书即未勾选），不能用勾选率当进度指标。"""
    spec = metrics_mod.measure_spec(ROOT / "specs" / "proposal.md")
    assert spec.acceptance_done == 0  # 说明勾选率恒为 0，故不作为指标


def test_acceptance_to_test_mapping_covers_most_criteria():
    """核心指标：验收标准被测试覆盖的比例。"""
    coverage = metrics_mod.map_acceptance_to_tests(ROOT)

    assert coverage.total >= 50
    assert coverage.ratio >= 0.9, f"覆盖率过低：{coverage.ratio}，未覆盖：{coverage.uncovered}"


def test_uncovered_criteria_are_only_known_non_mappable_kinds():
    """未覆盖项应只剩"非行为性"与"自指"两类，而不是功能缺口。"""
    coverage = metrics_mod.map_acceptance_to_tests(ROOT)

    for item in coverage.uncovered:
        assert ("安装" in item) or ("测试全部通过" in item), (
            f"出现了意料之外的未覆盖验收标准：{item}"
        )


def test_measure_project_reports_real_numbers():
    m = metrics_mod.measure_project(ROOT)

    assert m.spec_count == 3
    assert m.adr_count == 3
    assert m.test_count > 100
    assert m.code_lines > 1000
    assert m.tests_per_acceptance >= 1.0, "每个验收标准平均应至少对应 1 个测试"


def test_render_metrics_explains_checkbox_convention():
    """度量报告必须解释为什么不用勾选率，避免读者误解为进度。"""
    text = metrics_mod.render_metrics(metrics_mod.measure_project(ROOT))
    assert "规范格式" in text
    assert "误导性度量" in text
    assert "验收标准被测试覆盖" in text


def test_metric_sources_document_what_is_collectable():
    names = [name for name, _how, _expr in metrics_mod.METRIC_SOURCES]
    assert "规范覆盖率" in names
    assert "测试兑现度" in names
    # 明确标注哪些指标本仓库无法离线采集
    assert any("无法离线采集" in how for _n, how, _e in metrics_mod.METRIC_SOURCES)
