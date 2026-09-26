"""Task 2 验收标准：shared/ 四个共享模块的单元测试。

- config.py 能读取 config.yaml 并返回配置对象
- config.py 在配置缺少必填字段时抛出明确的错误信息
- logger.py 输出 JSON lines 格式日志
- errors.py 定义 CollectorError、GeneratorError、NotifierError 三个自定义异常
- storage.py 能创建 SQLite，写入和查询日报记录
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

import pytest

from shared.calendar import ensure_working_day, is_working_day
from shared.config import load_config
from shared.errors import (
    CollectorError,
    ConfigError,
    DailyReportError,
    GeneratorError,
    NonWorkingDayError,
    NotifierError,
)
from shared.logger import JsonLinesFormatter, get_logger
from shared.models import DailyReport, MemberReport
from shared.retry import retry_call
from shared.storage import ReportStorage

MINIMAL_YAML = """
members:
  - name: "张三"
    github: "zhangsan"
    lark: "zhangsan@company.com"
github:
  repos: ["acme/daily-report"]
lark:
  chat_id: "oc_1"
report:
  team_name: "平台研发组"
"""


# ---------------- config ----------------

def test_load_config_returns_config_object(tmp_dir):
    path = tmp_dir / "config.yaml"
    path.write_text(MINIMAL_YAML, encoding="utf-8")

    config = load_config(path)

    assert config.members[0].name == "张三"
    assert config.members[0].github == "zhangsan"
    assert config.github.repos == ["acme/daily-report"]
    assert config.report.team_name == "平台研发组"


def test_load_config_missing_members_raises_config_error(tmp_dir):
    path = tmp_dir / "config.yaml"
    path.write_text("github:\n  repos: []\n", encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    assert "members" in str(excinfo.value)


def test_load_config_missing_member_field_names_the_field(tmp_dir):
    path = tmp_dir / "config.yaml"
    path.write_text(
        'members:\n  - name: "张三"\n    github: "zhangsan"\n', encoding="utf-8"
    )

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    assert "members[0].lark" in str(excinfo.value)


def test_load_config_missing_file_raises_config_error(tmp_dir):
    with pytest.raises(ConfigError):
        load_config(tmp_dir / "nope.yaml")


def test_secrets_come_from_environment(tmp_dir, monkeypatch):
    """design.md §6.2：所有 API 密钥均通过环境变量注入。"""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_from_env")
    monkeypatch.setenv("SMTP_PASSWORD", "pw_from_env")
    path = tmp_dir / "config.yaml"
    path.write_text(MINIMAL_YAML, encoding="utf-8")

    config = load_config(path)

    assert config.github.token == "ghp_from_env"
    assert config.email.smtp_password == "pw_from_env"


def test_member_lookup_by_github_and_lark(tmp_dir):
    path = tmp_dir / "config.yaml"
    path.write_text(MINIMAL_YAML, encoding="utf-8")
    config = load_config(path)

    assert config.member_by_github("zhangsan").name == "张三"
    assert config.member_by_lark("zhangsan@company.com").name == "张三"
    assert config.member_by_github("nobody") is None


# ---------------- logger ----------------

def test_logger_emits_json_lines():
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="日报采集完成", args=(), exc_info=None,
    )
    record.source = "github"  # extra 字段

    line = JsonLinesFormatter().format(record)
    payload = json.loads(line)

    assert payload["level"] == "INFO"
    assert payload["message"] == "日报采集完成"
    assert payload["source"] == "github"
    assert "\n" not in line


def test_get_logger_is_idempotent():
    first = get_logger("test.idempotent")
    second = get_logger("test.idempotent")

    assert first is second
    assert len([h for h in first.handlers if isinstance(h, logging.StreamHandler)]) == 1


# ---------------- errors ----------------

def test_three_custom_exceptions_exist_and_share_base():
    assert issubclass(CollectorError, DailyReportError)
    assert issubclass(GeneratorError, DailyReportError)
    assert issubclass(NotifierError, DailyReportError)

    assert "[collector]" in str(CollectorError("超时"))
    assert "[generator]" in str(GeneratorError("渲染失败"))
    assert "[notifier]" in str(NotifierError("推送失败"))


def test_collector_error_accepts_custom_source():
    error = CollectorError("限流", source="github:acme/repo")
    assert "github:acme/repo" in str(error)


# ---------------- storage ----------------

def _sample_report(day: date = date(2026, 8, 20)) -> DailyReport:
    return DailyReport(
        date=day,
        team_name="平台研发组",
        members=[MemberReport(name="张三", github_username="zhangsan")],
        generated_at=datetime(2026, 8, 20, 18, 0),
        markdown="# 日报\n\n内容",
        html="<h1>日报</h1>",
    )


def test_storage_creates_database_and_round_trips(tmp_dir):
    db = tmp_dir / "nested" / "reports.db"

    with ReportStorage(db) as storage:
        assert db.exists()
        storage.save(_sample_report())

        row = storage.get(date(2026, 8, 20))

        assert row is not None
        assert row["team_name"] == "平台研发组"
        assert row["markdown"].startswith("# 日报")
        assert row["payload"]["members"][0]["name"] == "张三"


def test_storage_get_missing_date_returns_none(tmp_dir):
    with ReportStorage(tmp_dir / "reports.db") as storage:
        assert storage.get(date(2020, 1, 1)) is None


def test_storage_save_is_idempotent_per_date(tmp_dir):
    with ReportStorage(tmp_dir / "reports.db") as storage:
        storage.save(_sample_report())
        storage.save(_sample_report())

        assert storage.count() == 1


# ---------------- retry ----------------

def test_retry_call_succeeds_after_transient_failures(no_sleep):
    attempts = {"n": 0}

    def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise TimeoutError("超时")
        return "ok"

    assert retry_call(flaky, max_retries=3, interval=5.0, sleep=no_sleep) == "ok"
    assert attempts["n"] == 3
    assert no_sleep.calls == [5.0, 5.0]


def test_retry_call_raises_after_exhausting_retries(no_sleep):
    def always_fail() -> None:
        raise TimeoutError("一直超时")

    with pytest.raises(TimeoutError):
        retry_call(always_fail, max_retries=2, interval=0.0, sleep=no_sleep)


# ---------------- calendar ----------------

def test_weekend_and_holiday_are_not_working_days():
    assert is_working_day(date(2026, 8, 20)) is True  # 周四
    assert is_working_day(date(2026, 8, 22)) is False  # 周六
    assert is_working_day(date(2026, 8, 23)) is False  # 周日
    assert is_working_day(date(2026, 8, 20), ["2026-08-20"]) is False


def test_ensure_working_day_raises_on_weekend():
    with pytest.raises(NonWorkingDayError):
        ensure_working_day(date(2026, 8, 22))


# ---------------- 文本文件编码 ----------------

def test_spec_files_have_no_utf8_bom():
    """规范文件不得带 UTF-8 BOM。

    回归案例：`specs/tasks.md` 曾被写成带 BOM 的 UTF-8，于是首行
    变成 `\\ufeff# ...`，`sdd_agents/generate_review.py` 的
    `^#\\s+\\S`（re.MULTILINE，`^` 只认换行）匹配不到主标题，
    治理检查报「tasks.md 缺少文档主标题」，而文件肉眼完全正常。
    BOM 对 PowerShell 的 `Set-Content -Encoding UTF8` 是默认行为，
    这是最容易复发的一类静默故障。
    """
    project_root = Path(__file__).resolve().parent.parent
    checked = 0

    for path in sorted(project_root.glob("specs/**/*.md")):
        raw = path.read_bytes()
        checked += 1
        assert not raw.startswith(b"\xef\xbb\xbf"), (
            f"{path.relative_to(project_root)} 带 UTF-8 BOM，"
            f"会让规范解析器匹配不到首行标题"
        )

    assert checked >= 6, f"只检查到 {checked} 个规范文件，glob 可能失效"

