"""日报生成模块。

契约来源：specs/design.md §4.2 生成层接口契约 / specs/tasks.md Task 6
    generator.generate(members, date, team_name) -> DailyReport

验收标准（tasks.md Task 6）：
- [x] generate() 函数签名符合 design.md §4.2 的接口定义
- [x] 输出的 DailyReport.markdown 包含三个部分标题
- [x] 输出的 DailyReport.html 可被浏览器正确渲染
- [x] 若某成员当日无任何记录，日报中需要显示"今日无记录"
- [x] 若某数据源采集失败，日报中需要标注"数据获取失败"
- [x] Markdown 到 HTML 的转换格式正确
- [x] 使用 Mock 数据的单元测试全部通过

验收标准（tasks.md Task 6，v1.1 变更）：
- [x] 日报内容必须包含每位团队成员的工时统计
- [x] 当考勤数据不可用时，界面需要显示 "考勤数据暂不可用"
- [x] 布局顺序调整：工时统计模块应位于 "任务进展" 之后、"协作沟通" 之前

职责边界（design.md §2）：不做数据采集；不做 API 调用；不做推送。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Callable

from shared.errors import GeneratorError
from shared.logger import get_logger
from shared.models import (
    CollectResult,
    CommitRecord,
    DailyReport,
    MemberReport,
    MessageRecord,
    TaskRecord,
)

from generator import template

logger = get_logger("generator")


def aggregate(
    members: list,
    *,
    commits: CollectResult[CommitRecord] | None = None,
    tasks: CollectResult[TaskRecord] | None = None,
    messages: CollectResult[MessageRecord] | None = None,
    attendance: CollectResult | None = None,
) -> list[MemberReport]:
    """把三个数据源的采集结果按"人"聚合为 MemberReport 列表。

    聚合依赖 config.yaml 的成员身份映射表（书图 5-7）：
    CommitRecord.author ↔ members[].github
    TaskRecord.assignee / MessageRecord.sender / AttendanceRecord.employee_id ↔ members[].lark
    """
    reports: dict[str, MemberReport] = {}
    for member in members:
        reports[member.name] = MemberReport(
            name=member.name, github_username=member.github
        )

    by_github = {member.github: member.name for member in members}
    by_lark = {member.lark: member.name for member in members}

    if commits and commits.success:
        for record in commits.records:
            name = by_github.get(record.author)
            if name:
                reports[name].commits.append(record)

    if tasks and tasks.success:
        for record in tasks.records:
            name = by_lark.get(record.assignee)
            if name:
                reports[name].tasks.append(record)

    if messages and messages.success:
        for record in messages.records:
            name = by_lark.get(record.sender)
            if name:
                reports[name].messages.append(record)

    if attendance and attendance.success:
        for record in attendance.records:
            name = by_lark.get(record.employee_id)
            if name:
                reports[name].attendance = record

    return list(reports.values())


def generate(
    members: list[MemberReport],
    date: date,
    team_name: str,
    *,
    sources: dict[str, bool] | None = None,
    generated_at: datetime | None = None,
    now: Callable[[], datetime] | None = None,
) -> DailyReport:
    """把各成员的采集数据组织为日报。

    :param sources: 各数据源采集是否成功（用于标注"数据获取失败"，
        以及考勤采集失败时的"考勤数据暂不可用"）。
    :raises GeneratorError: 渲染失败。
    """
    if not members:
        raise GeneratorError("没有可生成日报的成员数据")

    # tasks.md Task 6 验收标准（v1.1）：考勤不可用时日报需显式标注。
    # 这里只判断采集是否失败，是否真的渲染标注由 template 依据"有无考勤数据"决定。
    attendance_failed = bool(sources) and sources.get(template.ATTENDANCE_SOURCE) is False

    try:
        markdown = template.render_markdown(
            members, date, team_name, sources=sources, attendance_failed=attendance_failed
        )
        html = template.markdown_to_html(markdown)
    except Exception as exc:  # noqa: BLE001 - 转换为领域异常
        raise GeneratorError(f"日报渲染失败：{exc}") from exc

    timestamp = generated_at or (now() if now else datetime.now())

    report = DailyReport(
        date=date,
        team_name=team_name,
        members=members,
        generated_at=timestamp,
        markdown=markdown,
        html=html,
    )
    logger.info(
        "日报生成完成",
        extra={
            "source": "generator",
            "members": len(members),
            "markdown_chars": len(markdown),
            "html_chars": len(html),
        },
    )
    return report
