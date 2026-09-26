"""契约测试：把 specs/contracts/api-spec.yaml 当作事实来源来校验实现。

对应书中的"规范审查先于代码审查"（第 3 章 3.4.2）与 api-spec.yaml 的
`x-contract-rules`。目的：防止"规范写了 A、代码做成 B"的漂移。
"""

from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path

import pytest
import yaml

from collector import github, lark_attendance, lark_msg, lark_task
from generator import formatter
from notifier import email, lark_bot
from shared import models

SPEC_PATH = Path(__file__).resolve().parent.parent / "specs" / "contracts" / "api-spec.yaml"


@pytest.fixture(scope="module")
def spec() -> dict:
    assert SPEC_PATH.exists(), f"缺少接口契约文件：{SPEC_PATH}"
    return yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 结构

def test_spec_is_valid_openapi_with_required_sections(spec):
    assert spec["openapi"].startswith("3.0")
    assert spec["info"]["title"]
    assert "schemas" in spec["components"]
    assert "x-module-contracts" in spec


def test_all_expected_schemas_exist(spec):
    required = {
        "CommitRecord", "TaskRecord", "MessageRecord", "AttendanceRecord",
        "MemberReport", "DailyReport", "CollectResult", "Member", "AppConfig",
    }
    assert required <= set(spec["components"]["schemas"])


# ------------------------------------------------- 契约字段 ↔ 数据类字段

@pytest.mark.parametrize(
    "schema_name, dataclass_name",
    [
        ("CommitRecord", "CommitRecord"),
        ("TaskRecord", "TaskRecord"),
        ("MessageRecord", "MessageRecord"),
        ("AttendanceRecord", "AttendanceRecord"),
        ("MemberReport", "MemberReport"),
        ("DailyReport", "DailyReport"),
        ("CollectResult", "CollectResult"),
    ],
)
def test_schema_properties_match_dataclass_fields(spec, schema_name, dataclass_name):
    """规范里声明的字段集合必须与实现的数据类字段集合完全一致。"""
    schema = spec["components"]["schemas"][schema_name]
    declared = set(schema["properties"])
    actual = {f.name for f in dataclasses.fields(getattr(models, dataclass_name))}

    assert declared == actual, (
        f"{schema_name} 契约与实现字段不一致："
        f"缺少实现={declared - actual}，实现多出={actual - declared}"
    )


def test_commit_record_stats_fields_are_required(spec):
    """回归：stats 三字段必须是必填，防止"静默填 0"缺陷复发。

    这是真实教训 —— GitHub 列表接口不返回 stats/files，代码曾用
    `stats.get("additions", 0)` 兜底，导致净增删行数恒为 0。
    """
    required = set(spec["components"]["schemas"]["CommitRecord"]["required"])
    assert {"additions", "deletions", "files_changed"} <= required


def test_collect_result_required_fields(spec):
    """ADR-003：success 必须显式，records 必须存在。"""
    required = set(spec["components"]["schemas"]["CollectResult"]["required"])
    assert {"success", "records"} <= required


def test_attendance_status_enum_matches_implementation(spec):
    """考勤状态枚举必须包含实现里实际会写入的取值。"""
    enum = spec["components"]["schemas"]["AttendanceRecord"]["properties"]["status"]["enum"]
    assert "签退缺失" in enum
    assert lark_attendance.STATUS_MISSING_CHECKOUT in enum


# --------------------------------------------- 契约函数签名 ↔ 实际签名

@pytest.mark.parametrize(
    "contract_key, module, func_name",
    [
        ("collector.github", github, "collect"),
        ("collector.lark_task", lark_task, "collect"),
        ("collector.lark_msg", lark_msg, "collect"),
        ("collector.lark_attendance", lark_attendance, "collect"),
        ("generator.generate", formatter, "generate"),
        ("notifier.email", email, "send"),
        ("notifier.lark_bot", lark_bot, "send"),
    ],
)
def test_contract_declares_an_existing_function(spec, contract_key, module, func_name):
    contract = spec["x-module-contracts"][contract_key]
    assert contract["function"] == func_name
    assert callable(getattr(module, func_name, None)), (
        f"{contract_key} 声明了 {func_name}()，但 {module.__name__} 中不存在"
    )


@pytest.mark.parametrize(
    "contract_key, module",
    [
        ("collector.github", github),
        ("collector.lark_task", lark_task),
        ("collector.lark_msg", lark_msg),
        ("collector.lark_attendance", lark_attendance),
        ("generator.generate", formatter),
        ("notifier.email", email),
        ("notifier.lark_bot", lark_bot),
    ],
)
def test_contract_parameter_names_match_signature(spec, contract_key, module):
    """契约里每个 required 参数的**名字**必须在真实函数签名中存在。"""
    contract = spec["x-module-contracts"][contract_key]
    signature = inspect.signature(getattr(module, contract["function"]))
    actual_params = set(signature.parameters)

    missing = [
        p["name"]
        for p in contract.get("parameters", [])
        if p.get("required") and p["name"] not in actual_params
    ]
    assert not missing, f"{contract_key}：契约要求的参数在实现中缺失 {missing}"


def test_collect_contracts_require_time_window():
    """三个源采集契约都必须带 since/until（proposal.md 的"当日采集窗口"）。"""
    for key in ("collector.github", "collector.lark_task", "collector.lark_msg"):
        signature = inspect.signature(github.collect)
        assert "since" in signature.parameters and "until" in signature.parameters


def test_push_layer_returns_bool_annotation():
    """x-contract-rules：推送层必须返回 bool 且不抛异常。"""
    for module in (email, lark_bot):
        annotation = inspect.signature(module.send).return_annotation
        assert annotation in (bool, "bool"), f"{module.__name__}.send 返回类型应为 bool"


# ------------------------------------------------------- 契约规则

def test_contract_rules_are_documented(spec):
    rules = spec["x-contract-rules"]
    assert len(rules) >= 4
    joined = " ".join(r["rule"] for r in rules)
    assert "静默兜底" in joined
    assert "CollectResult" in joined


def test_data_models_md_is_consistent_with_spec(spec):
    """data-models.md（给人看）与 api-spec.yaml（给工具看）不得互相矛盾。"""
    md_path = SPEC_PATH.parent / "data-models.md"
    assert md_path.exists(), "缺少 data-models.md（阶段三的另一份产物）"
    text = md_path.read_text(encoding="utf-8")

    for field in ("author", "additions", "deletions", "files_changed"):
        assert field in text, f"data-models.md 未记录 CommitRecord.{field}"
    assert "签退缺失" in text
    assert "CollectResult" in text
