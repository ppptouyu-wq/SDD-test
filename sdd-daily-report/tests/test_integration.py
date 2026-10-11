"""Task 10 验收标准：集成测试（端到端链路）。

- 端到端测试覆盖"采集→聚合→生成→推送"全链路（全部使用 Mock）
- 日报包含"代码提交""任务进展""协作沟通"三个核心板块
- 单一数据源失败时，其余数据源正常，且日报标注"数据获取失败"
- 成员当日无记录时显示"今日无记录"
- 所有数据源均失败时不生成空日报
- 测试全部通过
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

import main as app
from generator import template
from shared.errors import NonWorkingDayError
from shared.storage import ReportStorage

DAY = date(2026, 8, 20)   # 周四
SATURDAY = date(2026, 8, 22)
TZ = timezone(timedelta(hours=8))

GH_COMMIT = {
    "commit": {
        "message": "feat: 日报三段式编排",
        "author": {"name": "zhangsan", "date": "2026-08-20T10:12:00+08:00"},
    },
    "author": {"login": "zhangsan"},
    "stats": {"additions": 120, "deletions": 18},
    "files": [{"filename": "a.py"}, {"filename": "b.py"}, {"filename": "c.py"}, {"filename": "d.py"}],
}

LARK_TASK = {
    "summary": "日报模板评审",
    "status": "已完成",
    "status_from": "进行中",
    "updated_at": "2026-08-20T16:30:00+08:00",
    "assignees": [{"id": "lisi@company.com"}],
}

LARK_MESSAGE = {
    "sender": {"id": "wangwu@company.com"},
    "body": {"content": json.dumps({"text": "项目发布计划已同步"}, ensure_ascii=False)},
    "create_time": "1755676800000",
    "chat_name": "平台研发组",
}

ATTENDANCE_ITEM = {
    "employee_id": "lisi@company.com",
    "check_date": "2026-08-20",
    "check_in_time": "2026-08-20T09:32:00+08:00",
    "check_out_time": "2026-08-20T19:05:00+08:00",
    "status": "正常",
}

TOKEN_PAYLOAD = {"code": 0, "tenant_access_token": "t-abc", "expire": 7200}


class FakeResponse:
    """极简响应对象，供注入到采集层之外的位置。"""

    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {"code": 0}
        self.request = httpx.Request("POST", "https://example.com")

    def json(self) -> dict:
        return self._payload


def _wire_all_sources(monkeypatch, config, *, github_ok=True, lark_task_ok=True,
                      lark_msg_ok=True, attendance_ok=True):
    """把三个采集模块与两个推送模块全部替换为 Mock 实现。"""

    def github_collect(repos, since, until, *, config, client=None, sleep=None):  # noqa: ANN001
        from shared.models import CollectResult, CommitRecord

        if not github_ok:
            return CollectResult.fail("GitHub API 重试 3 次后仍失败：连接超时")
        return CollectResult.ok([
            CommitRecord(
                author="zhangsan", message=GH_COMMIT["commit"]["message"],
                timestamp=datetime(2026, 8, 20, 10, 12, tzinfo=TZ),
                repo="acme/daily-report", additions=120, deletions=18, files_changed=4,
            )
        ])

    def lark_task_collect(project_id, since, until, *, config, client=None,
                          token_manager=None, sleep=None):  # noqa: ANN001
        from shared.models import CollectResult, TaskRecord

        if not lark_task_ok:
            return CollectResult.fail("飞书任务 API 重试 3 次后仍失败：连接超时")
        return CollectResult.ok([
            TaskRecord(
                assignee="lisi@company.com", title="日报模板评审",
                status_from="进行中", status_to="已完成",
                updated_at=datetime(2026, 8, 20, 16, 30, tzinfo=TZ),
            )
        ])

    def lark_msg_collect(chat_id, keywords, since, until, *, config,
                         sensitive_keywords=None, client=None, token_manager=None,
                         sleep=None):  # noqa: ANN001
        from shared.models import CollectResult, MessageRecord

        if not lark_msg_ok:
            return CollectResult.fail("飞书消息 API 重试 3 次后仍失败：连接超时")
        return CollectResult.ok([
            MessageRecord(
                sender="wangwu@company.com", content="项目发布计划已同步",
                timestamp=datetime(2026, 8, 20, 15, 20, tzinfo=TZ), chat_name="平台研发组",
            )
        ])

    def attendance_collect(since, until, *, config, employee_type="employee_id",
                           user_ids=None, client=None, token_manager=None,
                           sleep=None):  # noqa: ANN001
        from shared.models import AttendanceRecord, CollectResult

        if not attendance_ok:
            return CollectResult.fail("飞书考勤 API 重试 3 次后仍失败：连接超时")
        return CollectResult.ok([
            AttendanceRecord(
                # 考勤按员工ID匹配（不是 lark/open_id）：李四 = members[].lark_employee_id
                employee_id="1002", date=DAY,
                check_in=datetime(2026, 8, 20, 9, 32, tzinfo=TZ),
                check_out=datetime(2026, 8, 20, 19, 5, tzinfo=TZ),
                work_hours=9.5, status="正常",
            )
        ])

    monkeypatch.setattr(app.github_collector, "collect", github_collect)
    monkeypatch.setattr(app.lark_task, "collect", lark_task_collect)
    monkeypatch.setattr(app.lark_msg, "collect", lark_msg_collect)
    monkeypatch.setattr(app.lark_attendance, "collect", attendance_collect)


def _run(config, monkeypatch, **kwargs):
    pushed: dict = {}

    def email_send(report, recipients, *, config, smtp_factory=None):  # noqa: ANN001
        pushed["email"] = report
        return True

    def bot_send(report, chat_id, *, config, **kw):  # noqa: ANN001
        pushed["lark_bot"] = report
        return True

    monkeypatch.setattr(app.email_notifier, "send", email_send)
    monkeypatch.setattr(app.lark_bot, "send", bot_send)

    with ReportStorage(config.storage_path) as storage:
        summary = app.run(config, day=DAY, storage=storage, **kwargs)
        row = storage.get(DAY)

    return summary, pushed, row


def test_end_to_end_full_pipeline(config, monkeypatch):
    """覆盖"采集→聚合→生成→推送"全链路。"""
    _wire_all_sources(monkeypatch, config)

    summary, pushed, row = _run(config, monkeypatch)

    assert summary["status"] == "ok"
    assert summary["members"] == 3
    assert summary["push"] == {"email": True, "lark_bot": True}
    assert set(pushed) == {"email", "lark_bot"}
    assert row is not None and row["date"] == DAY.isoformat()


def test_report_contains_three_core_sections(config, monkeypatch):
    """日报必须包含"代码提交""任务进展""协作沟通"三个核心板块。"""
    _wire_all_sources(monkeypatch, config)

    _, pushed, _ = _run(config, monkeypatch)
    markdown = pushed["email"].markdown

    assert "### 代码提交" in markdown
    assert "### 任务进展" in markdown
    assert "### 协作沟通" in markdown
    # 三个数据源的数据都进入了日报
    assert "feat: 日报三段式编排" in markdown
    assert "日报模板评审" in markdown
    assert "项目发布计划已同步" in markdown


def test_single_source_failure_does_not_block_others(config, monkeypatch):
    """单一数据源失败时，其余数据源正常，且日报标注"数据获取失败"。"""
    _wire_all_sources(monkeypatch, config, github_ok=False)

    summary, pushed, _ = _run(config, monkeypatch)
    markdown = pushed["email"].markdown

    assert summary["sources"]["GitHub"] is False
    assert summary["sources"]["飞书任务"] is True
    assert template.FETCH_FAILED in markdown
    assert "GitHub" in markdown
    # 其他数据源的内容仍然存在
    assert "日报模板评审" in markdown
    assert "项目发布计划已同步" in markdown


def test_member_without_records_shows_no_record(config, monkeypatch):
    """成员当日无记录时显示"今日无记录"。

    本场景下：张三只有 GitHub 提交、李四只有任务、王五只有消息，
    因此每个成员的其余板块都应为"今日无记录"。
    """
    _wire_all_sources(monkeypatch, config)

    _, pushed, _ = _run(config, monkeypatch)
    markdown = pushed["email"].markdown

    section = markdown.split("## 李四", 1)[1]
    section = section.split("\n## ", 1)[0]  # 只取李四这一节

    assert "### 代码提交" in section
    assert template.NO_RECORD in section      # 李四无代码提交、无消息
    # 李四确实有任务，所以任务板块不应是"今日无记录"
    task_block = section.split("### 任务进展", 1)[1].split("### ", 1)[0]
    assert "日报模板评审" in task_block


def test_all_sources_failed_does_not_generate_empty_report(config, monkeypatch):
    """所有数据源均失败时不生成空日报。"""
    _wire_all_sources(
        monkeypatch, config,
        github_ok=False, lark_task_ok=False, lark_msg_ok=False, attendance_ok=False,
    )

    summary, pushed, row = _run(config, monkeypatch)

    assert summary["status"] == "all_sources_failed"
    assert pushed == {}          # 没有推送
    assert row is None           # 没有落库


def test_non_working_day_is_skipped(config, monkeypatch):
    """识别非工作日（周末）并跳过日报生成。"""
    _wire_all_sources(monkeypatch, config)

    with ReportStorage(config.storage_path) as storage:
        with pytest.raises(NonWorkingDayError):
            app.run(config, day=SATURDAY, storage=storage)


def test_holiday_from_config_is_skipped(config, monkeypatch):
    """配置的法定节假日同样跳过。"""
    _wire_all_sources(monkeypatch, config)
    from dataclasses import replace

    holiday_config = replace(
        config, report=replace(config.report, holidays=[DAY.isoformat()])
    )

    with ReportStorage(config.storage_path) as storage:
        with pytest.raises(NonWorkingDayError):
            app.run(holiday_config, day=DAY, storage=storage)


def test_actionable_summary_reports_elapsed_and_sources(config, monkeypatch):
    """设计 §6.3：每次执行需记录各数据源采集条数与推送结果。"""
    _wire_all_sources(monkeypatch, config)

    summary, _, _ = _run(config, monkeypatch)

    assert set(summary["sources"]) == {"GitHub", "飞书任务", "飞书消息", "飞书考勤"}
    assert isinstance(summary["elapsed_seconds"], float)
    assert summary["push"] == {"email": True, "lark_bot": True}


# ============================================ 手动任务数据通道（端到端）
# 单元级的 load_tasks_file 解析测试见 tests/test_main.py；
# 这里验证"手动喂进来的任务"确实流经聚合层进入日报。


def test_tasks_file_flows_into_report(config, monkeypatch, tmp_dir):
    """端到端：手动任务数据必须真的出现在日报的「任务进展」板块。"""
    # 让飞书任务采集失败，从而确认日报里的任务是来自文件而非采集器
    _wire_all_sources(monkeypatch, config, lark_task_ok=False)

    path = tmp_dir / "tasks.json"
    path.write_text(
        json.dumps(
            [{"assignee": "lisi@company.com", "title": "手动录入的任务"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    pushed: dict = {}

    def email_send(report, recipients, *, config, smtp_factory=None):  # noqa: ANN001
        pushed["email"] = report
        return True

    monkeypatch.setattr(app.email_notifier, "send", email_send)
    monkeypatch.setattr(app.lark_bot, "send", lambda *a, **k: True)

    with ReportStorage(config.storage_path) as storage:
        summary = app.run(config, day=DAY, push=True, tasks_file=str(path), storage=storage)

    markdown = pushed["email"].markdown
    assert summary["sources"]["飞书任务"] is True  # 走文件通道，不再是失败
    assert "手动录入的任务" in markdown
    lisi_section = markdown.split("## 李四", 1)[1].split("\n## ", 1)[0]
    assert "手动录入的任务" in lisi_section


def test_tasks_file_unmapped_assignee_is_dropped(config, monkeypatch, tmp_dir):
    """不在成员映射表里的人应被聚合层丢弃 —— 与真实采集行为一致。"""
    _wire_all_sources(monkeypatch, config, lark_task_ok=False)

    path = tmp_dir / "tasks.json"
    path.write_text(
        json.dumps(
            [{"assignee": "陌生人@company.com", "title": "不该出现"}], ensure_ascii=False
        ),
        encoding="utf-8",
    )

    pushed: dict = {}
    monkeypatch.setattr(
        app.email_notifier,
        "send",
        lambda report, recipients, **k: (pushed.update(email=report), True)[1],
    )
    monkeypatch.setattr(app.lark_bot, "send", lambda *a, **k: True)

    with ReportStorage(config.storage_path) as storage:
        app.run(config, day=DAY, push=True, tasks_file=str(path), storage=storage)

    assert "不该出现" not in pushed["email"].markdown

