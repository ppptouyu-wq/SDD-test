"""Task 6 验收标准：日报生成模块单元测试。

- generate() 函数签名符合 design.md §4.2 的接口定义
- 输出的 DailyReport.markdown 包含三个部分标题
- 输出的 DailyReport.html 可被浏览器正确渲染
- 若某成员当日无任何记录，日报中需要显示"今日无记录"
- 若某数据源采集失败，日报中需要标注"数据获取失败"
- Markdown 到 HTML 的转换格式正确
- 使用 Mock 数据的单元测试全部通过

Task 6 验收标准（v1.1 变更）：
- 日报内容必须包含每位团队成员的工时统计
- 当考勤数据不可用时，界面需要显示"考勤数据暂不可用"
- 布局顺序调整：工时统计模块应位于"任务进展"之后、"协作沟通"之前
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from generator import formatter, template
from shared.config import Member
from shared.errors import GeneratorError
from shared.models import (
    AttendanceRecord,
    CollectResult,
    CommitRecord,
    DailyReport,
    MemberReport,
    MessageRecord,
    TaskRecord,
)

DAY = date(2026, 8, 20)


def _member_with_data(name: str = "张三", github: str = "zhangsan") -> MemberReport:
    return MemberReport(
        name=name,
        github_username=github,
        commits=[
            CommitRecord(
                author=github, message="feat: 日报三段式编排",
                timestamp=datetime(2026, 8, 20, 10, 12), repo="acme/daily-report",
                additions=120, deletions=18, files_changed=4,
            )
        ],
        tasks=[
            TaskRecord(
                assignee="lark_1", title="日报模板评审",
                status_from="进行中", status_to="已完成",
                updated_at=datetime(2026, 8, 20, 16, 30),
            )
        ],
        messages=[
            MessageRecord(
                sender="lark_1", content="项目日报推送时间定在 18:00",
                timestamp=datetime(2026, 8, 20, 15, 20), chat_name="平台研发组",
            )
        ],
    )


def test_generate_returns_daily_report_with_three_sections():
    report = formatter.generate([_member_with_data()], DAY, "平台研发组")

    assert isinstance(report, DailyReport)
    assert report.date == DAY
    assert report.team_name == "平台研发组"
    assert isinstance(report.generated_at, datetime)
    # 三个核心板块标题
    assert "### 代码提交" in report.markdown
    assert "### 任务进展" in report.markdown
    assert "### 协作沟通" in report.markdown
    # message
    assert "feat: 日报三段式编排" in report.markdown
    assert "日报模板评审" in report.markdown


def test_generate_produces_renderable_html():
    report = formatter.generate([_member_with_data()], DAY, "平台研发组")

    assert report.html.startswith("<!DOCTYPE html>")
    assert "<h1>" in report.html
    assert "<h3>代码提交</h3>" in report.html
    assert "<table class=\"report\">" in report.html
    assert report.html.rstrip().endswith("</html>")


def test_member_without_records_shows_no_record():
    """验收标准：若某成员当日无任何记录，日报中需要显示"今日无记录"。"""
    empty = MemberReport(name="王五", github_username="wangwu")

    report = formatter.generate([empty], DAY, "平台研发组")

    assert report.markdown.count(template.NO_RECORD) == 3  # 三个板块各一条
    assert template.NO_RECORD in report.html


def test_failed_source_is_marked_in_report():
    """验收标准：若某数据源采集失败，日报中需要标注"数据获取失败"。"""
    report = formatter.generate(
        [_member_with_data()],
        DAY,
        "平台研发组",
        sources={"GitHub": True, "飞书任务": False, "飞书消息": True},
    )

    assert template.FETCH_FAILED in report.markdown
    assert "飞书任务" in report.markdown
    assert template.FETCH_FAILED in report.html


def test_generate_rejects_empty_member_list():
    with pytest.raises(GeneratorError):
        formatter.generate([], DAY, "平台研发组")


# ---------------------------------------------- Task 6 验收标准（v1.1 变更）

def _member_with_attendance(name: str = "张三", github: str = "zhangsan") -> MemberReport:
    """带考勤记录的成员（v1.1 由 Task 11 采集）。"""
    return MemberReport(
        name=name,
        github_username=github,
        attendance=AttendanceRecord(
            employee_id="ou_test",
            date=DAY,
            check_in=datetime(2026, 4, 24, 9, 0),
            check_out=datetime(2026, 4, 24, 18, 30),
            work_hours=9.5,
            status="正常",
        ),
    )


def test_report_contains_work_hours_per_member():
    """验收标准（v1.1）：日报内容必须包含每位团队成员的工时统计。"""
    report = formatter.generate(
        [_member_with_attendance("张三"), _member_with_attendance("李四", "lisi")],
        DAY,
        "平台研发组",
        sources={"飞书考勤": True},
    )

    assert report.markdown.count("工时 9.5h") == 2
    assert "状态：正常" in report.markdown
    assert "工时 9.5h" in report.html


def test_attendance_unavailable_is_marked_in_report():
    """验收标准（v1.1）：当考勤数据不可用时，界面需要显示"考勤数据暂不可用"。"""
    report = formatter.generate(
        [_member_with_data()],
        DAY,
        "平台研发组",
        sources={"GitHub": True, "飞书考勤": False},
    )

    assert template.ATTENDANCE_UNAVAILABLE in report.markdown
    assert template.ATTENDANCE_UNAVAILABLE in report.html


def test_attendance_unavailable_not_reported_when_fetch_succeeded():
    """采集成功但成员当日无考勤，属于"今日无记录"，不得误报为系统故障。"""
    report = formatter.generate(
        [_member_with_data()],
        DAY,
        "平台研发组",
        sources={"GitHub": True, "飞书考勤": True},
    )

    assert template.ATTENDANCE_UNAVAILABLE not in report.markdown


def test_attendance_section_sits_between_tasks_and_messages():
    """验收标准（v1.1）：工时统计应位于"任务进展"之后、"协作沟通"之前。"""
    report = formatter.generate(
        [_member_with_attendance()],
        DAY,
        "平台研发组",
        sources={"飞书考勤": True},
    )

    # 必须找"### "开头的板块标题：总览表格的表头里也有"任务进展"四个字，
    # 若只搜裸词会命中表格（位置 54）而落在考勤段之前，得出错误的失败。
    i_tasks = report.markdown.index(f"### {template.SECTION_TASKS}")
    i_attendance = report.markdown.index("### 考勤")
    i_messages = report.markdown.index(f"### {template.SECTION_MESSAGES}")
    assert i_tasks < i_attendance < i_messages


def test_aggregate_maps_records_to_members_by_identity(config, ts):
    """聚合依赖 config.yaml 的成员身份映射表（书图 5-7）。"""
    commits = CollectResult.ok([
        CommitRecord(
            author="zhangsan", message="feat: A", timestamp=ts(10),
            repo="acme/daily-report", additions=1, deletions=1, files_changed=1,
        ),
        CommitRecord(
            author="unknown-user", message="feat: 未映射", timestamp=ts(11),
            repo="acme/daily-report", additions=1, deletions=1, files_changed=1,
        ),
    ])
    tasks = CollectResult.ok([
        TaskRecord(
            assignee="lisi@company.com", title="联调", status_from="待开始",
            status_to="进行中", updated_at=ts(12),
        )
    ])
    messages = CollectResult.ok([
        MessageRecord(
            sender="wangwu@company.com", content="发布计划同步",
            timestamp=ts(13), chat_name="平台研发组",
        )
    ])

    members = formatter.aggregate(
        config.members, commits=commits, tasks=tasks, messages=messages
    )
    by_name = {m.name: m for m in members}

    assert set(by_name) == {"张三", "李四", "王五"}
    assert len(by_name["张三"].commits) == 1          # author == github username
    assert by_name["张三"].tasks == []                 # 任务不归他
    assert len(by_name["李四"].tasks) == 1             # assignee == lark id
    assert len(by_name["王五"].messages) == 1          # sender == lark id
    # 未映射的提交被丢弃，不会凭空生成成员
    assert all(len(m.commits) <= 1 for m in members)


def test_aggregate_ignores_failed_sources(config, ts):
    failed = CollectResult.fail("API 超时")

    members = formatter.aggregate(config.members, commits=failed, tasks=failed, messages=failed)

    assert all(not m.commits and not m.tasks and not m.messages for m in members)


def test_markdown_to_html_converts_lists_bold_and_code():
    markdown = "# 标题\n\n- **粗体** 与 `code`\n- 第二项\n"

    html = template.markdown_to_html(markdown)

    assert "<h1>标题</h1>" in html
    assert "<ul>" in html and "</ul>" in html
    assert "<strong>粗体</strong>" in html
    assert "<code>code</code>" in html


def test_markdown_to_html_escapes_html_text():
    html = template.markdown_to_html("# <script>alert(1)</script>\n")

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_report_title_contains_date_and_team():
    """主题含日期与团队名（Task 7 验收标准的共享实现）。"""
    title = template.report_title("平台研发组", DAY)

    assert "平台研发组" in title
    assert "2026-08-20" in title


# ============================================ v1.3：身份映射扇出（Task 6 / Task 11）


def _attendance(employee_id: str, status: str = "正常") -> AttendanceRecord:
    return AttendanceRecord(
        employee_id=employee_id,
        date=date(2026, 10, 9),
        check_in=datetime(2026, 10, 9, 9, 0),
        check_out=datetime(2026, 10, 9, 18, 30),
        work_hours=9.5,
        status=status,
    )


def test_aggregate_fans_one_employee_id_out_to_every_matching_member():
    """同一飞书员工 ID 对应多个成员名时，考勤必须回填给**所有人**。

    场景来自真实配置：同一个人有两个 Git 作者名（例如网页端 `alice-web`、本地 `alice`），
    映射表里就是两个成员条目、飞书 ID 相同。若聚合层用 `{lark: name}` 这种后写覆盖的
    字典，只有最后一位成员能拿到考勤，另一位会被误显示成"今日无记录" —— 那是在撒谎。
    """
    members = [
        Member(name="我（网页端）", github="alice-web", lark="ou_x", lark_employee_id="1001"),
        Member(name="我（本地）", github="alice", lark="ou_x", lark_employee_id="1001"),
    ]

    got = formatter.aggregate(members, attendance=CollectResult.ok([_attendance("1001")]))
    by_name = {m.name: m for m in got}

    assert by_name["我（网页端）"].attendance is not None
    assert by_name["我（本地）"].attendance is not None, "同一员工ID必须扇出到所有匹配成员"
    assert by_name["我（本地）"].attendance.work_hours == 9.5


def test_aggregate_keeps_open_id_matching_separate_from_employee_id():
    """`lark`（open_id）管任务/消息，`lark_employee_id` 管考勤，两张表互不串味。

    若只用一张表，把 employee_id 当 open_id 用（或反过来），要么考勤匹配不上，
    要么任务被挂到同 ID 的另一个人头上。
    """
    members = [
        Member(name="张三", github="zhangsan", lark="ou_zhangsan", lark_employee_id="1001"),
        Member(name="李四", github="lisi-dev", lark="ou_lisi", lark_employee_id="1002"),
    ]
    tasks = CollectResult.ok([
        TaskRecord(
            assignee="ou_lisi",
            title="联调",
            status_from="待开始",
            status_to="进行中",
            updated_at=datetime(2026, 10, 9, 11, 0),
        )
    ])

    got = formatter.aggregate(
        members, tasks=tasks, attendance=CollectResult.ok([_attendance("1001", "缺勤")])
    )
    by_name = {m.name: m for m in got}

    assert len(by_name["李四"].tasks) == 1     # 任务按 open_id 命中李四
    assert by_name["张三"].tasks == []
    assert by_name["张三"].attendance is not None   # 考勤按 employee_id 命中张三
    assert by_name["张三"].attendance.status == "缺勤"
    assert by_name["李四"].attendance is None


def test_aggregate_does_not_let_blank_employee_ids_collide():
    """没配 `lark_employee_id` 的成员不能被空字符串串成同一个人。"""
    members = [
        Member(name="张三", github="zhangsan", lark="ou_a"),
        Member(name="李四", github="lisi-dev", lark="ou_b"),
    ]

    got = formatter.aggregate(members, attendance=CollectResult.ok([_attendance("")]))

    assert all(m.attendance is None for m in got)


def test_markdown_says_no_record_when_attendance_succeeded_but_empty():
    """考勤采集成功但当天没打卡 → "今日无记录"，不是"数据获取失败"，也不是留白。

    v1.3 回归背景：v1.1 的调用方没传名单，飞书回业务错误 employeeNos is empty，
    日报误报"数据获取失败"；把名单接上之后，飞书对"确实没打卡"返回的是
    code=0 + 空数组 —— 那时若照旧把考勤段整段省略，读者又会以为"这个模块没了"。
    """
    members = [MemberReport(name="张三", github_username="zhangsan")]

    markdown = template.render_markdown(
        members,
        date(2026, 10, 9),
        "平台研发组",
        sources={"飞书考勤": True},
        attendance_collected=True,
    )

    assert "### 考勤" in markdown
    assert template.NO_RECORD in markdown
    assert template.ATTENDANCE_UNAVAILABLE not in markdown


def test_markdown_says_unavailable_only_when_attendance_collection_failed():
    """采集失败才是"考勤数据暂不可用" —— 两个状态不许互换。"""
    members = [MemberReport(name="张三", github_username="zhangsan")]

    markdown = template.render_markdown(
        members,
        date(2026, 10, 9),
        "平台研发组",
        sources={"飞书考勤": False},
        attendance_failed=True,
        attendance_collected=False,
    )

    assert template.ATTENDANCE_UNAVAILABLE in markdown
