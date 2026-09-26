"""第 7.4.3 节四步法 —— 第三步补测试（1/2）：存量行为特征化测试。

"特征化测试"（characterization test）的作用是把**现有行为**固化成断言，
从而在后续重构时立刻发现"我改动了未曾预料的行为"。

这些测试只描述"现在是什么样"，不代表"应该是什么样" ——
后者由 `test_legacy_fixed_*.py` 按定稿契约验证。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from legacy.report.builder import ReportBuilder  # noqa: E402


def _records():
    return [
        {"who": "王五", "msg": "修了一个 bug", "repo": "r", "add": 3, "del": 1, "files": 1},
        {"who": "张三", "msg": "写了一个功能", "repo": "r", "add": 10, "del": 2, "files": 2},
        {"who": "张三", "msg": "又写了一个", "repo": "r", "add": 5, "del": 0, "files": 1},
    ]


def test_legacy_build_header_and_member_sections():
    builder = ReportBuilder("smtp", "u", "p")
    text = builder.build(_records(), "平台组")

    assert text.startswith("【平台组】今日工作")
    assert "== 张三 ==" in text
    assert "== 王五 ==" in text


def test_legacy_build_sorts_members_implicitly():
    """特征化：现有行为会对成员名排序（这是隐式决策，契约中要求显式声明）。"""
    builder = ReportBuilder("smtp", "u", "p")
    text = builder.build(_records(), "平台组")

    assert text.index("== 张三 ==") < text.index("== 王五 ==")


def test_legacy_build_truncates_long_message_at_60():
    """特征化：超过 60 字符的消息会被截断（魔法数字）。"""
    builder = ReportBuilder("smtp", "u", "p")
    long_msg = "很长的提交信息" * 20
    text = builder.build([{"who": "张三", "msg": long_msg, "add": 1, "del": 1}], "组")

    line = [ln for ln in text.splitlines() if ln.startswith("- ")][0]
    body = line[len("- ") : line.rindex(" (+")]
    assert len(body) == 60 + len("...")
    assert body.endswith("...")


def test_legacy_build_caps_items_per_member_at_200():
    """特征化：单成员最多 200 条（魔法数字）。"""
    builder = ReportBuilder("smtp", "u", "p")
    many = [{"who": "张三", "msg": f"提交 {i}", "add": 1, "del": 0} for i in range(250)]

    text = builder.build(many, "组")

    assert text.count("- 提交") == 200


def test_legacy_build_empty_records_returns_header_only():
    builder = ReportBuilder("smtp", "u", "p")
    text = builder.build([], "空组")

    assert text.strip() == "【空组】今日工作"


def test_legacy_build_mixes_responsibilities():
    """特征化（反面样本）：构造函数持有 SMTP 凭据 —— 生成与推送耦合。

    这条测试记录的是**问题**，不是期望。修正版通过职责分离解决它。
    """
    builder = ReportBuilder("smtp.example.com", "u@x.com", "secret")

    assert builder.smtp_host == "smtp.example.com"
    assert builder.smtp_pass == "secret"
