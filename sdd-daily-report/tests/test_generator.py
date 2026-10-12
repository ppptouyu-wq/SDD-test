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
    """v1.6：邮件正文由 render_email_html() 按 design.md §6.7 的卡片版式渲染。"""
    report = formatter.generate([_member_with_data()], DAY, "平台研发组")

    assert report.html.startswith("<!DOCTYPE html>")
    assert "平台研发组工作日报" in report.html
    assert "📊 今日概览" in report.html
    assert "📌 <strong>今日总结：</strong>" in report.html
    assert "📝 代码提交" in report.html
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


def test_email_html_has_the_four_presentation_blocks():
    """标题区 / 今日概览 / 今日总结 / 按成员分组，四段都要在。"""
    report = formatter.generate([_member_with_data()], DAY, "平台研发组")
    html = report.html

    assert "平台研发组工作日报" in html
    assert f"日期：{DAY.isoformat()}" in html
    assert "📊 今日概览" in html
    assert "📌 <strong>今日总结：</strong>" in html
    assert "👤 张三" in html
    assert "主要变更：" in html


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
    """采集失败要显式告警；某板块为空渲染「今日无记录」而不是留白（§6.7(5)）。"""
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
    # 三个板块各自有「今日无记录」，考勤是"数据获取失败"三态里的失败态
    assert report.html.count("今日无记录") >= 3
    assert template.ATTENDANCE_UNAVAILABLE in report.html

