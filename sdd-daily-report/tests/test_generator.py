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

import copy
import re
from datetime import date, datetime
from pathlib import Path

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
    RepoProgress,
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
                status="done",
                created_at=datetime(2026, 8, 20, 9, 40),
                due_at=None,
                completed_at=datetime(2026, 8, 20, 16, 30),
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
    """v1.6：邮件正文由 render_email_html() 按 design.md §6.7 的卡片版式渲染。"""
    report = formatter.generate([_member_with_data()], DAY, "平台研发组")

    assert report.html.startswith("<!DOCTYPE html>")
    # v1.7：报告名不再拼「工作日报」后缀，标题区是「报告名 + 日期同行」
    assert ">平台研发组<span" in report.html
    assert "2026-08-20" in report.html
    assert "平台研发组工作日报" not in report.html
    assert "📊 今日概览" in report.html
    assert "📌 <strong>今日总结：</strong>" in report.html
    assert "📝 代码提交" in report.html
    assert report.html.rstrip().endswith("</html>")


def test_member_without_records_shows_no_record():
    """验收标准：某成员当日无任何记录时，各板块给出**自己的**空态说明（v1.10）。

    v1.10 之前四格统一渲染「今日无记录」，读者看不出空的是提交还是数据；
    现在每格说清"空的是什么"，且四句话必须互不相同。
    """
    empty = MemberReport(name="王五", github_username="wangwu")

    report = formatter.generate([empty], DAY, "平台研发组")

    for text in (
        template.NO_COMMITS,
        template.NO_TASKS,
    ):
        assert text in report.markdown
        assert text in report.html
    # v1.15：协作沟通只在 Markdown 里，邮件的空态跟着板块一起撤掉
    assert template.NO_MESSAGES in report.markdown
    assert template.NO_MESSAGES not in report.html
    # 四段空态文案互不相同（用同一句话打发就等于没说明白空的是什么）
    assert len({template.NO_COMMITS, template.NO_TASKS, template.NO_MESSAGES}) == 3
    # 日报正文不再使用展示层口径的通用空态
    assert template.NO_RECORD not in report.markdown


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


def test_attendance_span_says_which_side_is_missing():
    """v1.8.4：缺签到就写"无签到"，不能借用状态名把左右颠倒。

    旧实现把 `None` 一律写成"签退缺失"，于是"缺签到、有签退"会被渲染成
    `（签退缺失 - 20:24）` —— 与状态栏自相矛盾。
    """
    member = MemberReport(
        name="张三",
        github_username="zhangsan",
        attendance=AttendanceRecord(
            employee_id="emp_0001",
            date=DAY,
            check_in=None,
            check_out=datetime(2026, 10, 13, 20, 24, 54),
            work_hours=0.0,
            status="签到缺失",
        ),
    )
    report = formatter.generate([member], DAY, "平台研发组", sources={"飞书考勤": True})

    assert "状态：签到缺失" in report.markdown
    assert "（无签到 - 20:24）" in report.markdown
    assert "签退缺失 - 20:24" not in report.markdown
    assert "（无签到 - 20:24）" in report.html

    # 反向：有签到、缺签退
    member.attendance = AttendanceRecord(
        employee_id="emp_0001",
        date=DAY,
        check_in=datetime(2026, 10, 13, 9, 0),
        check_out=None,
        work_hours=0.0,
        status="签退缺失",
    )
    report = formatter.generate([member], DAY, "平台研发组", sources={"飞书考勤": True})

    assert "（09:00 - 无签退）" in report.markdown
    assert template._attendance_span(member.attendance) == "09:00 - 无签退"


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
            assignee="lisi@company.com", title="联调", status="todo",
            created_at=ts(10), due_at=None, completed_at=None, updated_at=ts(12),
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
    """主题含日期与报告名（Task 7 验收标准的共享实现）。

    v1.7：报告名由部署配置给出完整名称（例「孔子博智能日报」），模板**不再**拼「工作日报」
    后缀 —— 否则会变成「孔子博智能日报工作日报」。日期必须保留，收件箱里要能分辨是哪天。
    """
    title = template.report_title("孔子博智能日报", date(2026, 10, 11))

    assert title == "孔子博智能日报 2026-10-11"
    assert "工作日报" not in title
    assert "平台研发组" in template.report_title("平台研发组", DAY)
    assert "2026-08-20" in template.report_title("平台研发组", DAY)


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
            status="todo",
            created_at=datetime(2026, 10, 9, 9, 0),
            due_at=None,
            completed_at=None,
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
    """考勤采集成功但当天没打卡 → 「当日无打卡记录」，不是"数据获取失败"，也不是留白。

    v1.3 回归背景：v1.1 的调用方没传名单，飞书回业务错误 employeeNos is empty，
    日报误报"数据获取失败"；把名单接上之后，飞书对"确实没打卡"返回的是
    code=0 + 空数组 —— 那时若照旧把考勤段整段省略，读者又会以为"这个模块没了"。
    v1.10 起这句话说清缺的是"打卡"，不再借用通用的"今日无记录"。
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
    assert template.NO_ATTENDANCE_RECORD in markdown
    assert template.NO_RECORD not in markdown
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


# ---------------------------------------------------------------------------
# Task 6 验收标准（v1.5 变更）：提交信息只呈现「标题 + 正文摘要」
#
# 回归背景：本项目自己的提交正文（按书图 7-8 闭环写的规范链说明）有几十行，
# 原先 template 直接把 commit.message 整段贴进「代码提交」段，日报被撑爆。
# 规范落点：proposal.md §3.1（v1.5）、design.md §3.3、tasks.md Task 6 v1.5。
# ---------------------------------------------------------------------------


def _long_message() -> str:
    return (
        "feat(v1.5): 收紧提交信息的呈现\n"
        "\n"
        "第一句说明这次改了什么。第二句说明为什么这么改。第三句收尾。\n"
        "第四句属于正文后面，不该进日报。第五句同前。\n"
    )


def test_summarize_commit_keeps_subject_and_first_three_sentences():
    subject, summary = template.summarize_commit(_long_message())

    assert subject == "feat(v1.5): 收紧提交信息的呈现"
    assert summary == "第一句说明这次改了什么。第二句说明为什么这么改。第三句收尾。"
    assert "第四句" not in summary


def test_summarize_commit_returns_only_subject_when_body_is_missing():
    """GitHub 网页端上传文件产生的提交没有正文 → 摘要为空，不造占位符。"""
    assert template.summarize_commit("Add files via upload") == (
        "Add files via upload",
        "",
    )
    # 只有空行的"正文"同样算没有正文
    assert template.summarize_commit("chore: x\n\n\n") == ("chore: x", "")
    assert template.summarize_commit("") == ("", "")


def test_summarize_commit_does_not_split_on_dots_inside_identifiers():
    """`main.py`、`v1.2`、`99992402` 里的点不是句末，不能被切碎。"""
    message = "fix: x\n\n改动 main.py 与 v1.2 契约。第二句。"

    subject, summary = template.summarize_commit(message)
    assert subject == "fix: x"
    assert summary == "改动 main.py 与 v1.2 契约。第二句。"

    # 只取一句时也不能把 main.py 切开
    _, first_only = template.summarize_commit(message, max_sentences=1)
    assert first_only == "改动 main.py 与 v1.2 契约。"


def test_summarize_commit_truncates_an_endless_sentence_at_the_char_limit():
    """整段没有句号时的兜底：按字符截断并补省略号，别把邮件撑爆。"""
    message = "feat: x\n\n" + "啊" * 500

    _, summary = template.summarize_commit(message)

    assert len(summary) == template.COMMIT_SUMMARY_MAX_CHARS + 1
    assert summary.endswith(template.COMMIT_SUMMARY_ELLIPSIS)
    assert summary.startswith("啊" * 10)


def test_markdown_renders_commit_summary_under_the_subject_and_drops_the_body():
    member = _member_with_data()
    member.commits[0].message = _long_message()

    markdown = template.render_markdown([member], DAY, "平台研发组")

    assert "feat(v1.5): 收紧提交信息的呈现" in markdown
    assert "第一句说明这次改了什么。" in markdown
    # 完整正文不许进日报
    assert "第四句属于正文后面" not in markdown
    # 摘要渲染成标题下方缩进的子列表项
    assert f"  - {template.summarize_commit(_long_message())[1]}" in markdown


def test_markdown_to_html_renders_the_indented_commit_summary_as_a_list_item():
    """缩进的子列表项必须变成嵌套 <li>：不能退化成 <p>，也不能拍平成同级项（design.md §3.3 第 7 条）。"""
    member = _member_with_data()
    member.commits[0].message = _long_message()

    markdown = template.render_markdown([member], DAY, "平台研发组")
    html = template.markdown_to_html(markdown)

    assert "<li>第一句说明这次改了什么。第二句说明为什么这么改。第三句收尾。</li>" in html
    assert "  - 第一句" not in html
    assert "第四句属于正文后面" not in html


def test_html_keeps_each_commit_visually_separated():
    """相邻提交在 HTML 里必须是各自的顶层项，摘要嵌在自己的父项内（v1.5.1 呈现缺陷修复）。"""
    member = _member_with_data()
    member.commits[0].message = _long_message()
    second = copy.deepcopy(member.commits[0])
    second.message = "feat(v1.4): 日报不再仅限工作日，双休日与节假日照常生成\n\n正文说明第一句。正文说明第二句。"
    member.commits.append(second)

    markdown = template.render_markdown([member], DAY, "平台研发组")
    html = template.markdown_to_html(markdown)
    commits_html = html.split(f"<h3>{template.SECTION_COMMITS}</h3>", 1)[1].split(
        f"<h3>{template.SECTION_TASKS}</h3>", 1
    )[0]

    # 每条提交是一个顶层 <li>，摘要的嵌套列表紧跟在它的父项内部
    assert commits_html.count("<ul>") == 1
    assert commits_html.count('<ul class="sub">') == 2
    assert re.search(
        r'<li>[^\n]*\n<ul class="sub">\n<li>', commits_html
    ), commits_html
    assert commits_html.count("</li>") == 4  # 2 个顶层提交项 + 各自的 1 条摘要
    # 摘要不许跑到父项外面变成同级项
    assert "</li>\n<li>第一句" not in commits_html
    # 顶层条目之间要有间距，摘要要比标题弱
    assert "ul > li { margin-top: 10px" in html
    assert "ul.sub > li { margin-top: 4px" in html


# ---------------------------------------------------------------------------
# v1.6：邮件日报的卡片式版式（design.md §6.7）
# ---------------------------------------------------------------------------


def test_email_html_has_the_five_presentation_blocks():
    """标题区 / 今日概览 / 今日总结 / 今日工程进展（v1.8）/ 按成员分组，五段都要在。"""
    report = formatter.generate(
        [_member_with_data()], DAY, "平台研发组", progress=_progress()
    )
    html = report.html

    # v1.7：标题区把原「标题 + 信息块」合并成一行（报告名 + 日期同行，生成时间靠右）
    assert ">平台研发组<span" in html
    assert f"　{DAY.isoformat()}</span>" in html
    assert "生成于 " in html
    assert "📊 今日概览" in html
    assert "📌 <strong>今日总结：</strong>" in html
    # v1.8：工程进展段夹在「今日总结」与成员分组之间
    assert template.SECTION_PROGRESS in html
    assert html.index("📌 <strong>今日总结：</strong>") < html.index(template.SECTION_PROGRESS)
    assert html.index(template.SECTION_PROGRESS) < html.index("👤 张三")
    assert "👤 张三" in html
    # v1.15：邮件撤掉「主要变更」逐条清单，只留 per-repo 汇总句
    assert "主要变更：" not in html
    assert "共提交 1 次" in html  # repo_sentence 的汇总句仍在


def test_email_overview_counts_and_summary_come_from_real_data():
    """概览计数与总结句必须现算，不得由 AI 生成（proposal.md §3.1 v1.6）。"""
    member = _member_with_data()
    report = formatter.generate([member, _member_with_data("李四", "lisi")], DAY, "平台研发组")

    assert template.overview_counts(report) == (2, 2, 2)
    # 三张计数卡的数字与标签
    assert '>2</div>' in report.html
    assert ">代码提交</div>" in report.html
    assert ">任务更新</div>" in report.html
    assert ">协作讨论</div>" in report.html
    # 总结句按计数拼出来（确定性模板句）
    assert template.summary_sentence(2, 2, 2) == "共完成 2 次代码提交，2 项任务有更新，2 条协作讨论，整体进展活跃。"
    assert "共完成 2 次代码提交，2 项任务有更新，2 条协作讨论" in report.html


def test_email_summary_sentence_stays_calm_when_there_is_nothing():
    assert template.summary_sentence(0, 0, 0).startswith("当日未采集到")
    assert template.summary_sentence(1, 0, 0).endswith("整体进展平稳。")


def test_email_repo_sentence_aggregates_commits_by_repository():
    member = _member_with_data()
    second = copy.deepcopy(member.commits[0])
    second.additions, second.deletions = 5, 1
    member.commits.append(second)
    other = copy.deepcopy(member.commits[0])
    other.repo, other.additions, other.deletions = "acme/other", 10, 2
    member.commits.append(other)

    sentence = template.repo_sentence(member)

    assert "在 acme/daily-report 仓库共提交 2 次，累计 +125/-19 行" in sentence
    assert "在 acme/other 仓库共提交 1 次，累计 +10/-2 行" in sentence


def test_email_html_stays_friendly_to_mail_clients():
    """卡片与配色靠表格 + 内联样式；不引外部资源、不依赖 JS（design.md §6.7(4)）。"""
    report = formatter.generate([_member_with_data()], DAY, "平台研发组")
    html = report.html

    assert '<table role="presentation"' in html
    assert 'style="' in html
    assert "url(" not in html          # 不引用外部图片/字体
    assert "<script" not in html       # 不依赖 JavaScript
    assert "<img" not in html


def test_email_html_marks_failed_sources_and_empty_sections():
    """采集失败要显式告警；某板块为空时给出该板块自己的空态说明，而不是留白（§6.7(5)）。"""
    member = _member_with_data()
    member.commits, member.tasks, member.messages = [], [], []

    report = formatter.generate(
        [member],
        DAY,
        "平台研发组",
        sources={"GitHub": True, "飞书考勤": False, "飞书任务": True},
    )

    assert "⚠️ 数据获取失败：飞书考勤" in report.html
    assert template.overview_counts(report) == (0, 0, 0)
    # v1.10：三格各说自己的话（不再统一"今日无记录"），考勤是"数据获取失败"三态里的失败态
    assert template.NO_COMMITS in report.html
    assert template.NO_TASKS in report.html
    # v1.15：邮件不再渲染「协作沟通」板块，所以连它的空态也不该出现
    assert template.NO_MESSAGES not in report.html
    assert template.SECTION_MESSAGES not in report.html
    assert template.NO_RECORD not in report.html
    assert template.ATTENDANCE_UNAVAILABLE in report.html


# ---------------------------------------------------------------------------
# v1.15：邮件板块精简（撤掉提交明细与「协作沟通」；design.md §6.7(5)）
# ---------------------------------------------------------------------------


def test_email_drops_commit_detail_list_and_keeps_the_totals_sentence():
    """邮件只报"几次提交、多少行"，不再逐条列提交标题（proposal.md §3.1 v1.15）。"""
    report = formatter.generate([_member_with_data()], DAY, "平台研发组")
    html = report.html

    assert "本日 在 acme/daily-report 仓库共提交 1 次" in html
    assert "累计 +120/-18 行" in html
    # 逐条明细的痕迹一个都不该留：标题、元信息里的"个文件"、正文摘要
    assert "主要变更：" not in html
    assert "feat: 日报三段式编排" not in html
    assert "个文件" not in html


def test_email_drops_collaboration_section_while_markdown_keeps_it():
    """渠道差异化：同一份 DailyReport，邮件没有协作沟通，Markdown 与展示页仍有。"""
    report = formatter.generate([_member_with_data()], DAY, "平台研发组")

    assert template.SECTION_MESSAGES not in report.html
    assert "项目日报推送时间定在 18:00" not in report.html
    # Markdown 侧完全不受影响（§3.1 明写"仍保留在飞书 Markdown 日报"）
    assert "### 协作沟通" in report.markdown
    assert "项目日报推送时间定在 18:00" in report.markdown


def test_email_still_warns_when_message_source_fails_after_dropping_the_section():
    """不渲染 ≠ 不采集：协作沟通撤了，飞书消息采集失败照样要在顶部如实告警。"""
    report = formatter.generate(
        [_member_with_data()],
        DAY,
        "平台研发组",
        sources={"GitHub": True, "飞书任务": True, "飞书消息": False},
    )

    assert "⚠️ 数据获取失败：飞书消息" in report.html
    assert template.SECTION_MESSAGES not in report.html


# ---------------------------------------------------------------------------
# v1.7：邮件版式换色与结构微调（design.md §6.7）
# ---------------------------------------------------------------------------


def test_email_html_only_uses_the_design_palette():
    """邮件 HTML 里出现的颜色必须落在 §6.7 的色板内（v1.7 起由白名单守住）。

    这条是"配色别跑偏"的执行性约束：以后谁想随手加个颜色，先得过这一关。
    """
    report = formatter.generate(
        [_member_with_data()],
        DAY,
        "平台研发组",
        sources={"GitHub": True, "飞书考勤": False},
    )
    colors = set(re.findall(r"#[0-9a-fA-F]{6}", report.html))

    assert colors, "版式里应当有颜色"
    assert colors <= set(template.EMAIL_PALETTE)
    # 展示层的身份红是大面积告警色，邮件里不得出现（§6.7(1)）
    assert "#d32029" not in colors


def test_email_html_uses_the_teal_identity_and_member_bar():
    """v1.7 换色：身份色是青碧；计数卡是浅青底 + 顶部深青条；成员分组用整宽名条。"""
    report = formatter.generate([_member_with_data()], DAY, "平台研发组")
    html = report.html

    assert template.EMAIL_BRAND == "#0f766e"
    assert template.EMAIL_BRAND_DEEP == "#0b5b55"
    assert f"border-top:3px solid {template.EMAIL_BRAND}" in html
    assert f"background:{template.EMAIL_BRAND_DEEP}" in html        # 顶部通栏
    assert f'color:{template.EMAIL_BRAND_DEEP};">👤 张三' in html   # 成员名条


# ---------------------------------------------------------------------------
# v1.8：今日工程进展板块 + 邮件主题模板（design.md §3.4 / §6.7(2) / §6.8）
# ---------------------------------------------------------------------------


def _progress(**overrides) -> RepoProgress:
    values = {
        "commit_count": 3,
        "version": "v1.8",
        "change_layer": "设计变更",
        "spec_changed": ["specs/design.md", "specs/contracts/data-models.md"],
        "code_changed": True,
        "same_commit": True,
        "spec_coverage": "97/98 = 99.0%",
        "last_commit_date": "",
    }
    values.update(overrides)
    return RepoProgress(**values)


def _plain(html: str) -> str:
    """去掉标签只看文字 —— 断言盯"说了什么"，不盯"用哪个标签强调"。"""
    return re.sub(r"<[^>]+>", "", html)


def test_email_html_renders_the_engineering_progress_block():
    """第 4 段把当日可复现的事实逐条写出来（proposal.md §3.1 v1.8）。"""
    report = formatter.generate([_member_with_data()], DAY, "平台研发组", progress=_progress())
    html = report.html
    text = _plain(html)

    assert template.SECTION_PROGRESS in html
    assert "当日 3 次提交" in text
    assert "迭代到 v1.8" in text
    assert "设计变更" in text
    assert "起点在 design.md" in text          # 变更起点说人话
    assert "specs/design.md" in text           # 规范链改动清单
    assert "97/98 = 99.0%" in text             # 规范覆盖率


def test_email_progress_block_is_honest_about_a_missing_closed_loop():
    """闭环文案要贴合事实：分两次提交不许写成"同一个提交"。"""
    split = formatter.generate(
        [_member_with_data()], DAY, "平台研发组",
        progress=_progress(same_commit=False),
    ).html
    code_only = formatter.generate(
        [_member_with_data()], DAY, "平台研发组",
        progress=_progress(change_layer="缺陷修复", spec_changed=[], code_changed=True),
    ).html

    assert "闭环：规范与代码分在两个提交" in split
    assert "本次只改代码与测试（未触及规范）" in code_only
    assert "闭环：规范与代码在同一个提交" not in code_only


def test_email_html_shows_a_neutral_placeholder_without_progress():
    """取不到仓库信息时渲染中性空态，而不是留白或报"采集失败"（§6.8(4)）。"""
    report = formatter.generate([_member_with_data()], DAY, "平台研发组")

    assert report.progress is None
    assert template.SECTION_PROGRESS in report.html
    assert template.PROGRESS_EMPTY in report.html
    # 它不是"数据获取失败"：工程数据源刻意不进 sources 四态（§3.4）
    assert template.FETCH_FAILED not in report.html


def test_email_progress_shows_the_last_commit_date_when_the_day_is_empty():
    """当日 0 次提交时补一行「最近一次提交」，让"今天没动"与"停了三周"能区分（v1.10）。"""
    html = formatter.generate(
        [_member_with_data()],
        DAY,
        "平台研发组",
        progress=_progress(
            commit_count=0,
            version="",
            change_layer="未识别",
            spec_changed=[],
            code_changed=False,
            same_commit=False,
            last_commit_date="2026-10-13",
        ),
    ).html
    text = _plain(html)

    assert "当日 0 次提交" in text
    assert "最近一次提交：2026-10-13" in text


def test_email_progress_omits_the_last_commit_line_when_there_are_commits_today():
    """当日有提交时不出现「最近一次提交」——"最近一次"就是今天，没有增量信息（v1.10）。"""
    html = formatter.generate(
        [_member_with_data()],
        DAY,
        "平台研发组",
        progress=_progress(last_commit_date="2026-10-13"),
    ).html

    assert "当日 3 次提交" in _plain(html)
    assert "最近一次提交" not in _plain(html)


def test_email_progress_never_invents_a_last_commit_date():
    """取不到日期（空串）时不渲染这一行，更不许臆造一个日期填上（v1.10）。"""
    html = formatter.generate(
        [_member_with_data()],
        DAY,
        "平台研发组",
        progress=_progress(
            commit_count=0, version="", spec_changed=[], code_changed=False,
            same_commit=False, last_commit_date="",
        ),
    ).html

    assert "最近一次提交" not in _plain(html)


def test_email_progress_block_keeps_using_the_design_palette():
    """新板块只许用 §6.7 色板内的颜色，不许自己造一个蓝。"""
    html = formatter.generate(
        [_member_with_data()], DAY, "平台研发组", progress=_progress()
    ).html
    colors = set(re.findall(r"#[0-9a-fA-F]{6}", html))

    assert colors <= set(template.EMAIL_PALETTE)


def test_report_title_follows_the_configured_subject_pattern():
    """主题模板可配；默认保持旧格式（Task 7 v1.8 验收标准）。"""
    assert template.report_title("平台研发组", DAY) == "平台研发组 2026-08-20"
    assert (
        template.report_title(
            "孔子博智能日报",
            date(2026, 10, 11),
            subject_pattern="{grade}+{owner}+{date}+智能日报",
            grade="研一",
            owner="孔子博",
        )
        == "研一+孔子博+2026-10-11+智能日报"
    )


def test_unknown_subject_placeholders_are_kept_verbatim():
    """不认识的占位符原样保留，不抛异常、不静默吃掉。"""
    title = template.report_title(
        "平台研发组", DAY, subject_pattern="{team_name} {date} {sprint}"
    )

    assert title == "平台研发组 2026-08-20 {sprint}"


def test_report_title_never_hardcodes_personal_attributes():
    """年级、姓名这类个人属性只能来自配置，模板代码里不许出现（proposal.md §3.1 v1.8）。"""
    source = Path(template.__file__).read_text(encoding="utf-8")

    assert "研一" not in source
    assert "孔子博" not in source


# ---- v1.9：概览计数卡下方的一行「当日代码变更行数」 ----
# 触发：老师要求在邮件日报里"加一行能看见当天代码迭代提交的行数"。
# 起点判定为需求变更（呈现什么内容属产品行为）→ proposal.md §3.1 → design.md §6.7(2)。


def test_email_change_line_sums_lines_across_members_and_repositories():
    """行数汇总要跨成员、跨仓库合计，并紧跟三张计数卡（proposal.md §3.1 v1.9）。"""
    first = _member_with_data()
    second = _member_with_data("李四", "lisi")
    extra = copy.deepcopy(first.commits[0])
    extra.repo, extra.additions, extra.deletions = "acme/other", 7, 3
    second.commits.append(extra)

    report = formatter.generate([first, second], DAY, "平台研发组")
    html = report.html

    assert template.commit_line_totals(report) == (247, 39)  # 120+120+7 / 18+18+3
    assert "当日代码变更" in html
    assert "+247" in html
    assert "-39" in html
    # 顺序：三张计数卡 → 这一行 → 今日总结（补的是"量"，不能跑到总结后面）
    assert html.index(">代码提交</div>") < html.index("当日代码变更")
    assert html.index("当日代码变更") < html.index("今日总结")


def test_email_change_line_shows_zeros_when_there_is_no_commit():
    """当日没有提交时显示 +0 / -0 行，不隐藏、不留白（proposal.md §3.1 v1.9）。"""
    member = _member_with_data()
    member.commits = []

    report = formatter.generate([member], DAY, "平台研发组")

    assert template.commit_line_totals(report) == (0, 0)
    assert "当日代码变更" in report.html
    assert "+0" in report.html
    assert "-0" in report.html


# ---- v1.11：正文标题区的标题与邮件主题同源 ----
# 触发：老师要求「主题把中间的 + 去掉，正文首页那个标题也改成一样的」。
# 判定为需求变更（呈现什么内容属产品行为）→ proposal.md §3.1 → design.md §6.7(2)。


def test_email_header_title_is_the_subject_when_provided():
    """传入 title 时，标题区与 <title> 都必须是主题那一串，且不再另挂日期（proposal.md §3.1 v1.11）。"""
    title = template.report_title(
        "平台研发组",
        DAY,
        subject_pattern="{grade}{owner}{date}智能日报",
        grade="研一",
        owner="张三",
    )
    report = formatter.generate([_member_with_data()], DAY, "平台研发组", title=title)

    assert title == "研一张三2026-08-20智能日报"
    assert f">{title}</td>" in report.html
    assert f"<title>{title}</title>" in report.html
    # 标题里本来就含日期，不能再挂一个日期片段（否则会读成两个名字）
    assert f"{title}<span" not in report.html
    assert ">平台研发组<span" not in report.html


def test_email_header_falls_back_to_team_name_and_date_without_title():
    """title 为空串时回退到 v1.7 的形态，旧调用方不受影响（design.md §6.7(2)）。"""
    report = formatter.generate([_member_with_data()], DAY, "平台研发组")

    assert ">平台研发组<span" in report.html
    assert "<title>平台研发组 2026-08-20</title>" in report.html


# ---------------- v1.16：任务行 = 真实字段（design.md §3.6、§6.7(5)）----------------


def _mock_member_with_tasks() -> MemberReport:
    """一个成员 + 两条任务：一条已完成、一条未完成（带截止日期）。"""
    return MemberReport(
        name="张三",
        github_username="zhangsan",
        commits=[],
        tasks=[
            TaskRecord(
                assignee="ou_zhangsan",
                title="写周报",
                status="done",
                created_at=datetime(2026, 8, 19, 9, 0),
                due_at=None,
                completed_at=datetime(2026, 8, 20, 16, 30),
                updated_at=datetime(2026, 8, 20, 16, 30),
            ),
            TaskRecord(
                assignee="ou_zhangsan",
                title="联调接口",
                status="todo",
                created_at=datetime(2026, 8, 18, 10, 0),
                due_at=datetime(2026, 8, 22, 18, 0),
                completed_at=None,
                updated_at=datetime(2026, 8, 20, 11, 0),
            ),
        ],
        messages=[],
    )


def test_task_line_renders_title_date_and_completion():
    """标题｜创建 MM-DD｜已完成/未完成：三件事各自都在（design.md §3.6）。"""
    done, todo = _mock_member_with_tasks().tasks

    assert template.task_line(done) == "写周报｜创建 08-19｜已完成 08-20"
    assert template.task_line(todo) == "联调接口｜创建 08-18｜未完成｜截止 08-22"


def test_task_line_falls_back_when_dates_are_missing():
    """时间戳缺失时不留"None"，也不留悬空分隔符。"""
    bare = TaskRecord(
        assignee="ou_x",
        title="无时间任务",
        status="todo",
        created_at=None,
        due_at=None,
        completed_at=None,
        updated_at=datetime(2026, 8, 20, 9, 0),
    )

    assert template.task_line(bare) == "无时间任务｜未完成"


def test_markdown_and_email_show_tasks_with_completion_state():
    """Markdown 与邮件两条路径都画同一份任务行，读者看到的是"做完没有"。"""
    report = formatter.generate([_mock_member_with_tasks()], DAY, "平台研发组")

    assert "- 写周报｜创建 08-19｜已完成 08-20" in report.markdown
    assert "- 联调接口｜创建 08-18｜未完成｜截止 08-22" in report.markdown
    assert "写周报｜创建 08-19｜已完成 08-20" in report.html
    assert "联调接口｜创建 08-18｜未完成｜截止 08-22" in report.html
    # 旧版的"状态流转"写法必须彻底消失
    assert "进行中 →" not in report.markdown
    assert "→ 已完成" not in report.html


