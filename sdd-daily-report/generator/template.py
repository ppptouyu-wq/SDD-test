"""日报模板管理。

契约来源：specs/tasks.md Task 6
    "输出到 generator/template.py：日报模板管理"

本书未给出模板的具体样式，此处按 design.md §1 的"管道式架构"与
proposal.md §2.1 的"三段式编排（代码提交→任务进展→协作沟通）"实现。
Jinja2 用于 HTML 邮件模板（pyproject.toml 依赖之一）。
"""

from __future__ import annotations

import re
from datetime import date, datetime

from jinja2 import Template

from shared.models import CommitRecord, DailyReport, MemberReport

NO_RECORD = "今日无记录"
FETCH_FAILED = "数据获取失败"
# tasks.md Task 6 验收标准（v1.1）：考勤数据不可用时显式标注，不静默留白
ATTENDANCE_UNAVAILABLE = "考勤数据暂不可用"
# 采集层数据源键名之一，用于判断考勤采集失败（与 main.py SOURCE_LARK_ATTENDANCE 一致）
ATTENDANCE_SOURCE = "飞书考勤"

# 提交信息在日报里的呈现上限（proposal.md §3.1 v1.5 / design.md §3.3）：
# 正文摘要最多保留 3 句、总长不超过 200 字符，超出截断并补省略号。
COMMIT_SUMMARY_MAX_SENTENCES = 3
COMMIT_SUMMARY_MAX_CHARS = 200
COMMIT_SUMMARY_ELLIPSIS = "…"

# 三个核心板块（proposal.md §3.1："生成的日报必须包含三个核心板块"）
SECTION_COMMITS = "代码提交"
SECTION_TASKS = "任务进展"
SECTION_MESSAGES = "协作沟通"


def _first_paragraph(text: str) -> str:
    """取第一个非空连续段落，段内换行按 Markdown 软换行的惯例用单个空格接续。"""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            lines.append(line)
        elif lines:
            break
    return " ".join(lines)


def _take_sentences(paragraph: str, limit: int) -> str:
    """从段落开头取最多 limit 句，返回原文切片（空格与标点原样保留）。

    中文句末标点 ``。！？!?`` 直接断句；英文句点只在后面是空白或已到段尾时才算句末，
    否则 ``main.py``、``v1.2``、``99992402`` 会被切碎。
    """
    count = 0
    for index, char in enumerate(paragraph):
        is_end = char in "。！？!?"
        is_dot = char == "." and (
            index + 1 == len(paragraph) or paragraph[index + 1].isspace()
        )
        if is_end or is_dot:
            count += 1
            if count >= limit:
                return paragraph[: index + 1].strip()
    return paragraph.strip()


def summarize_commit(
    message: str,
    *,
    max_sentences: int = COMMIT_SUMMARY_MAX_SENTENCES,
    max_chars: int = COMMIT_SUMMARY_MAX_CHARS,
) -> tuple[str, str]:
    """把完整提交信息拆成（标题, 摘要）。

    标题取第一行原文；摘要取正文第一个非空段落的前 ``max_sentences`` 句，再按
    ``max_chars`` 兜底截断（防止"一整段没有句号"的提交把邮件撑爆）。

    没有正文时摘要返回空串——日报只显示标题，不造占位符，也不把上一条提交的内容
    串下来（design.md §3.3，v1.5）。完整原文仍保存在 ``CommitRecord.message`` 里。
    """
    if not message:
        return "", ""
    lines = message.splitlines()
    subject = lines[0].strip()
    paragraph = _first_paragraph("\n".join(lines[1:]))
    if not paragraph:
        return subject, ""
    summary = _take_sentences(paragraph, max_sentences) if max_sentences > 0 else ""
    if len(summary) > max_chars:
        summary = summary[:max_chars].rstrip() + COMMIT_SUMMARY_ELLIPSIS
    return subject, summary


def report_title(team_name: str, day: date) -> str:
    """邮件主题包含日期和团队名称（tasks.md Task 7 验收标准）。"""
    return f"[{team_name}] {day.isoformat()} 工作日报"


def render_markdown(
    members: list[MemberReport],
    day: date,
    team_name: str,
    *,
    sources: dict[str, bool] | None = None,
    attendance_failed: bool = False,
    attendance_collected: bool = False,
) -> str:
    """渲染 Markdown 格式日报。

    :param sources: 各数据源采集是否成功，用于顶部"数据获取失败"标注。
    :param attendance_failed: 考勤采集是否失败。为 True 且成员无考勤记录时，
        渲染"考勤数据暂不可用"（tasks.md Task 6 v1.1 验收标准），
        而不是把考勤段整段留白——留白会被误读为"当日确实没有考勤"。
    :param attendance_collected: 考勤采集是否**成功**（v1.3）。为 True 且成员无考勤
        记录时渲染"今日无记录"：这是"当天没打卡"，与"采集失败"是两件事，
        不能因为采到了空数组就把整段藏起来。
    """
    lines: list[str] = [f"# {team_name}工作日报 — {day.isoformat()}", ""]

    # 只有"采集失败且确实没有任何考勤数据"时才标注不可用。
    # 若采集成功但成员当日无考勤，属于"今日无记录"而非系统故障，不能混为一谈。
    show_unavailable = attendance_failed and not any(
        m.attendance is not None for m in members
    )

    if sources:
        failed = [name for name, ok in sources.items() if not ok]
        if failed:
            lines.append(f"> ⚠️ {FETCH_FAILED}：{'、'.join(failed)}")
            lines.append("")

    lines.append("## 总览")
    lines.append("")
    lines.append("| 成员 | 代码提交 | 任务进展 | 协作沟通 |")
    lines.append("|---|---|---|---|")
    for member in members:
        lines.append(
            f"| {member.name} | {len(member.commits)} | {len(member.tasks)} | {len(member.messages)} |"
        )
    lines.append("")

    for member in members:
        lines.append(f"## {member.name}")
        lines.append("")
        lines.append(f"### {SECTION_COMMITS}")
        if member.commits:
            for commit in member.commits:
                # 只贴「标题 + 正文摘要」：完整提交信息（规范链说明那种几十行正文）
                # 会把日报撑爆，收件人读不下去（design.md §3.3，v1.5）。
                subject, summary = summarize_commit(commit.message)
                lines.append(
                    f"- `{commit.repo}` {subject} "
                    f"（+{commit.additions}/-{commit.deletions}，{commit.files_changed} 个文件）"
                )
                if summary:
                    lines.append(f"  - {summary}")
        else:
            lines.append(f"- {NO_RECORD}")
        lines.append("")

        lines.append(f"### {SECTION_TASKS}")
        if member.tasks:
            for task in member.tasks:
                lines.append(f"- {task.title}：{task.status_from} → {task.status_to}")
        else:
            lines.append(f"- {NO_RECORD}")
        lines.append("")

        # 考勤段的位置由 tasks.md Task 6（v1.1）规定：
        # "布局顺序调整：工时统计模块应位于'任务进展'之后、'协作沟通'之前"。
        # 因此它必须插在 tasks 与 messages 两段之间，不能追加到成员段落末尾。
        if member.attendance is not None:
            attendance = member.attendance
            lines.append("### 考勤")
            lines.append(
                f"- 工时 {attendance.work_hours:.1f}h，状态：{attendance.status} "
                f"（{_time_text(attendance.check_in)} - {_time_text(attendance.check_out)}）"
            )
            lines.append("")
        elif show_unavailable:
            # Task 6 验收标准（v1.1）：考勤采集失败时明确标注，避免读者误以为当日无考勤
            lines.append("### 考勤")
            lines.append(f"- {ATTENDANCE_UNAVAILABLE}")
            lines.append("")
        elif attendance_collected:
            # Task 6 验收标准（v1.3）：采集成功但本人无记录 = 当天没打卡，
            # 说"今日无记录"。整段留白会被误读成"这个模块不存在"，
            # 也与展示层（webview 显示"今日无记录"）对不上。
            lines.append("### 考勤")
            lines.append(f"- {NO_RECORD}")
            lines.append("")

        lines.append(f"### {SECTION_MESSAGES}")
        if member.messages:
            for message in member.messages:
                lines.append(f"- [{message.chat_name}] {message.content}")
        else:
            lines.append(f"- {NO_RECORD}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _time_text(value: object) -> str:
    if value is None:
        return "签退缺失"
    return value.strftime("%H:%M")  # type: ignore[union-attr]


def markdown_to_html(markdown: str) -> str:
    """把本模块生成的 Markdown 转换为 HTML。

    只支持本项目实际用到的语法（标题、无序列表、表格、粗体、行内代码、引用、水平线），
    以保证输出"可被浏览器正确渲染"且不引入额外依赖。

    提交摘要渲染成标题下方**缩进的子列表项**（design.md §3.3），因此这里按
    "去掉前导空白后以 `- ` 开头"来识别列表项；**缩进 ≥2 空格的项会嵌套进父 `<li>`**
    （不能拍平成同级项，否则收件人分不清哪句摘要属于哪条提交，见 design.md §3.3 第 7 条）。
    """
    html: list[str] = []
    in_list = False
    in_sub = False
    in_table = False
    item_open = False  # 顶层 <li> 已写出开始标签、还没写 </li>

    def close_item() -> None:
        nonlocal item_open, in_sub
        if in_sub:
            html.append("</ul>")
            in_sub = False
        if item_open:
            html.append("</li>")
            item_open = False

    def close_blocks() -> None:
        nonlocal in_list, in_table
        close_item()
        if in_list:
            html.append("</ul>")
            in_list = False
        if in_table:
            html.append("</tbody></table>")
            in_table = False

    def inline(text: str) -> str:
        text = _escape(text)
        text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
        text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
        return text

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue  # 分隔行
            if not in_table:
                close_blocks()
                html.append('<table class="report"><tbody>')
                in_table = True
                html.append(
                    "<tr>" + "".join(f"<th>{inline(c)}</th>" for c in cells) + "</tr>"
                )
            else:
                html.append(
                    "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells) + "</tr>"
                )
            continue

        if stripped.startswith("- "):
            indent = len(line) - len(line.lstrip(" "))
            if in_table:
                close_blocks()
            if indent >= 2 and item_open:
                # 缩进子项：挂进上一个顶层 <li> 内部（design.md §3.3 第 7 条）
                if not in_sub:
                    html.append('<ul class="sub">')
                    in_sub = True
                html.append(f"<li>{inline(stripped[2:])}</li>")
                continue
            close_item()
            if not in_list:
                html.append("<ul>")
                in_list = True
            html.append(f"<li>{inline(stripped[2:])}")
            item_open = True
            continue

        close_blocks()

        if not line:
            continue
        if line.startswith("#### "):
            html.append(f"<h4>{inline(line[5:])}</h4>")
        elif line.startswith("### "):
            html.append(f"<h3>{inline(line[4:])}</h3>")
        elif line.startswith("## "):
            html.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("# "):
            html.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("> "):
            html.append(f"<blockquote>{inline(line[2:])}</blockquote>")
        elif line.strip() == "---":
            html.append("<hr/>")
        else:
            html.append(f"<p>{inline(line)}</p>")

    close_blocks()
    body = "\n".join(html)
    return HTML_TEMPLATE.render(title="工作日报", body=body)


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ---------------------------------------------------------------------------
# 邮件日报的可视化版式（design.md §6.7，v1.6）
#
# 邮件是独立画布：会被转发到任意客户端、主要在手机上读，所以不引用展示层的
# `ant` 令牌（§6.6(4)），单独用下面这套"阅读型"色板。红色 `--accent` 不参与——
# 大面积红色在邮件里等于"告警"，而日报是日常汇报。
# ---------------------------------------------------------------------------
EMAIL_BRAND = "#1a6cf5"        # 标题、概览数字、品牌短线
EMAIL_BRAND_SOFT = "#e8f1fd"   # 信息块 / 总结块底色
EMAIL_NEUTRAL_SOFT = "#f6f8fb"  # 「今日无记录」等中性块底色
EMAIL_BORDER = "#e3e6ea"       # 卡片边框与分隔线
EMAIL_FG = "#1f2329"           # 主文本
EMAIL_FG_2 = "#646a73"         # 次级文本
EMAIL_PAGE_BG = "#f2f3f5"      # 外层页面底色
EMAIL_DANGER = "#cf1322"       # 采集失败（沿用 §6.1 的失败语义）
EMAIL_DANGER_SOFT = "#fdecec"

EMAIL_FONT = (
    "-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',"
    "Arial,'Helvetica Neue',sans-serif"
)


def _datetime_text(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value is not None else ""


def overview_counts(report: DailyReport) -> tuple[int, int, int]:
    """当日团队级三项计数：代码提交 / 任务更新 / 协作讨论（design.md §6.7(2)）。"""
    commits = sum(len(member.commits) for member in report.members)
    tasks = sum(len(member.tasks) for member in report.members)
    messages = sum(len(member.messages) for member in report.members)
    return commits, tasks, messages


def summary_sentence(commits: int, tasks: int, messages: int) -> str:
    """今日总结句。

    这是**确定性模板句**，不是 AI 生成内容：`proposal.md` §2.2 明确把"AI 摘要"排除在外，
    所以这里只做计数与措辞拼接，不引入任何模型（design.md §6.7(3)）。
    """
    if not (commits or tasks or messages):
        return "当日未采集到代码提交、任务更新或协作讨论，成员日报以考勤与状态为主。"
    parts: list[str] = []
    if commits:
        parts.append(f"{commits} 次代码提交")
    if tasks:
        parts.append(f"{tasks} 项任务有更新")
    if messages:
        parts.append(f"{messages} 条协作讨论")
    level = "整体进展活跃。" if (commits + tasks + messages) >= 5 else "整体进展平稳。"
    return "共完成 " + "，".join(parts) + "，" + level


def repo_sentence(member: MemberReport) -> str:
    """某成员的提交仓库汇总句：「本日 在 X 仓库共提交 N 次，累计 +A/-B 行。」"""
    totals: dict[str, list[int]] = {}
    for commit in member.commits:
        slot = totals.setdefault(commit.repo, [0, 0, 0])
        slot[0] += 1
        slot[1] += commit.additions
        slot[2] += commit.deletions
    pieces = [
        f"在 {repo} 仓库共提交 {count} 次，累计 +{added}/-{removed} 行"
        for repo, (count, added, removed) in totals.items()
    ]
    return "本日 " + "；".join(pieces) + "。"


def _email_section_title(emoji: str, text: str) -> str:
    return (
        f'<div style="font-size:15px;font-weight:700;color:{EMAIL_FG};'
        f'border-bottom:1px solid {EMAIL_BORDER};padding-bottom:8px;margin:22px 0 12px;">'
        f"{emoji} {text}</div>"
    )


def _email_callout(inner: str, *, background: str = EMAIL_BRAND_SOFT, accent: str = EMAIL_BRAND) -> str:
    """左侧带色边的提示块（信息块 / 总结块 / 告警块 / 中性块共用）。"""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:12px 0;"><tr>'
        f'<td style="border-left:4px solid {accent};background:{background};'
        f'padding:12px 16px;border-radius:0 8px 8px 0;font-size:14px;line-height:1.7;'
        f'color:{EMAIL_FG};">{inner}</td></tr></table>'
    )


def _email_stat_card(value: int, label: str) -> str:
    """今日概览里的一张计数卡（三张并排，靠外层表格的 td 实现分栏）。"""
    return (
        '<td align="center" valign="top" width="33.33%" style="padding:0 5px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background:#ffffff;border:1px solid {EMAIL_BORDER};border-radius:10px;">'
        '<tr><td align="center" style="padding:16px 6px 14px;">'
        f'<div style="font-size:28px;line-height:1.1;font-weight:700;color:{EMAIL_BRAND};">'
        f"{value}</div>"
        f'<div style="font-size:13px;color:{EMAIL_FG_2};margin-top:6px;">{label}</div>'
        "</td></tr></table></td>"
    )


def _email_commit_item(commit: CommitRecord, subject: str, summary: str) -> str:
    """一条提交：标题 + 仓库/行数元信息 + 摘要（摘要弱化，design.md §3.3）。"""
    body = [
        f'<div style="font-size:14px;font-weight:600;line-height:1.6;color:{EMAIL_FG};">'
        f"{_escape(subject)}</div>",
        f'<div style="font-size:12px;color:{EMAIL_FG_2};margin-top:4px;">'
        f"{_escape(commit.repo)} · "
        f"+{commit.additions} / -{commit.deletions} · "
        f"{commit.files_changed} 个文件</div>",
    ]
    if summary:
        body.append(
            f'<div style="font-size:13px;color:{EMAIL_FG_2};line-height:1.7;margin-top:6px;">'
            f"{_escape(summary)}</div>"
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        "<tr>"
        f'<td width="16" valign="top" style="font-size:14px;line-height:1.7;color:{EMAIL_BRAND};">•</td>'
        f'<td style="padding-bottom:14px;">{"".join(body)}</td>'
        "</tr></table>"
    )


def _email_plain_list(items: list[str]) -> str:
    rows = "".join(
        f'<div style="font-size:14px;line-height:1.75;color:{EMAIL_FG};margin-top:6px;">'
        f'<span style="color:{EMAIL_BRAND};">•</span> {item}</div>'
        for item in items
    )
    return rows


def render_email_html(report: DailyReport) -> str:
    """渲染邮件日报的 HTML（design.md §6.7，v1.6）。

    与 `markdown_to_html()` 的区别：这里**直接按结构渲染**。原因是新版式要做汇总 ——
    "概览计数""今日总结句""每个成员在哪些仓库提交了多少次、累计多少行"都要在
    `DailyReport` 上现算，从已经拍平的 Markdown 反推既脆弱又不诚实。
    Markdown 仍然是落库的原件（`report.markdown`），HTML 只是它的另一种呈现。
    """
    sources = report.sources or {}
    attendance_failed = bool(sources) and sources.get(ATTENDANCE_SOURCE) is False
    attendance_collected = bool(sources) and sources.get(ATTENDANCE_SOURCE) is True
    show_unavailable = attendance_failed and not any(
        member.attendance is not None for member in report.members
    )

    commits, tasks, messages = overview_counts(report)
    day_text = _escape(report.date.isoformat())
    blocks: list[str] = []

    # ---- 1. 标题区 ----
    blocks.append(
        f'<div style="font-size:24px;line-height:1.3;font-weight:700;color:{EMAIL_BRAND};">'
        f"{_escape(report.team_name)}工作日报</div>"
        f'<div style="height:3px;width:56px;background:{EMAIL_BRAND};'
        f'border-radius:2px;margin:12px 0 0;"></div>'
        f'<div style="font-size:13px;color:{EMAIL_FG_2};margin-top:12px;">{day_text}</div>'
    )

    # ---- 2. 信息块 ----
    blocks.append(
        _email_callout(
            f"日期：{day_text}　|　生成时间：{_escape(_datetime_text(report.generated_at))}",
            background=EMAIL_NEUTRAL_SOFT,
        )
    )

    # 采集失败的来源必须显式列出，严禁静默跳过（§6.1）
    failed = [name for name, ok in sources.items() if not ok]
    if failed:
        blocks.append(
            _email_callout(
                f"⚠️ {FETCH_FAILED}：{_escape('、'.join(failed))}",
                background=EMAIL_DANGER_SOFT,
                accent=EMAIL_DANGER,
            )
        )

    # ---- 3. 今日概览 ----
    blocks.append(_email_section_title("📊", "今日概览"))
    blocks.append(
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        "<tr>"
        + _email_stat_card(commits, "代码提交")
        + _email_stat_card(tasks, "任务更新")
        + _email_stat_card(messages, "协作讨论")
        + "</tr></table>"
    )

    # ---- 4. 今日总结 ----
    blocks.append(
        _email_callout(
            "📌 <strong>今日总结：</strong>"
            + _escape(summary_sentence(commits, tasks, messages))
        )
    )

    # ---- 5. 按成员分组 ----
    for member in report.members:
        blocks.append(
            f'<div style="font-size:18px;font-weight:700;color:{EMAIL_FG};'
            f'margin:30px 0 0;">👤 {_escape(member.name)}</div>'
        )

        blocks.append(_email_section_title("📝", SECTION_COMMITS))
        if member.commits:
            blocks.append(_email_callout(_escape(repo_sentence(member))))
            blocks.append(
                f'<div style="font-size:14px;font-weight:700;color:{EMAIL_FG};'
                f'margin:16px 0 10px;">主要变更：</div>'
            )
            blocks.extend(
                _email_commit_item(commit, *summarize_commit(commit.message))
                for commit in member.commits
            )
        else:
            blocks.append(
                _email_callout(
                    NO_RECORD, background=EMAIL_NEUTRAL_SOFT, accent=EMAIL_BORDER
                )
            )

        blocks.append(_email_section_title("✅", SECTION_TASKS))
        if member.tasks:
            blocks.append(
                _email_plain_list(
                    [
                        f"{_escape(task.title)}：{_escape(task.status_from)} → "
                        f"{_escape(task.status_to)}"
                        for task in member.tasks
                    ]
                )
            )
        else:
            blocks.append(
                _email_callout(
                    NO_RECORD, background=EMAIL_NEUTRAL_SOFT, accent=EMAIL_BORDER
                )
            )

        # 考勤放在"任务进展"之后、"协作沟通"之前（tasks.md Task 6 v1.1 规定的顺序）
        if member.attendance is not None:
            attendance = member.attendance
            blocks.append(_email_section_title("🕘", "考勤"))
            blocks.append(
                f'<div style="font-size:14px;line-height:1.75;color:{EMAIL_FG};">'
                f"工时 {attendance.work_hours:.1f}h，状态：{_escape(attendance.status)}"
                f"（{_time_text(attendance.check_in)} - {_time_text(attendance.check_out)}）</div>"
            )
        elif show_unavailable:
            blocks.append(_email_section_title("🕘", "考勤"))
            blocks.append(
                _email_callout(
                    ATTENDANCE_UNAVAILABLE,
                    background=EMAIL_DANGER_SOFT,
                    accent=EMAIL_DANGER,
                )
            )
        elif attendance_collected:
            blocks.append(_email_section_title("🕘", "考勤"))
            blocks.append(
                _email_callout(
                    NO_RECORD, background=EMAIL_NEUTRAL_SOFT, accent=EMAIL_BORDER
                )
            )

        blocks.append(_email_section_title("💬", SECTION_MESSAGES))
        if member.messages:
            blocks.append(
                _email_plain_list(
                    [
                        f'<span style="color:{EMAIL_FG_2};">[{_escape(message.chat_name)}]</span> '
                        f"{_escape(message.content)}"
                        for message in member.messages
                    ]
                )
            )
        else:
            blocks.append(
                _email_callout(
                    NO_RECORD, background=EMAIL_NEUTRAL_SOFT, accent=EMAIL_BORDER
                )
            )

    return _email_document(
        f"{report.team_name}工作日报 {report.date.isoformat()}",
        "\n".join(blocks),
    )


def _email_document(title: str, inner: str) -> str:
    """把版式内容包进邮件友好的 HTML 文档（表格骨架 + 内联样式，design.md §6.7(4)）。"""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{_escape(title)}</title>
<style>
  /* 渐进增强：被剥掉也不影响布局与配色（配色与分栏都在内联样式里） */
  body {{ margin: 0; padding: 0; background: {EMAIL_PAGE_BG}; }}
  .wrap {{ border-radius: 12px; }}
  .content {{ font-family: {EMAIL_FONT}; line-height: 1.7; }}
  @media (max-width: 480px) {{
    .wrap {{ width: 100% !important; }}
    .pad {{ padding: 18px !important; }}
  }}
</style>
</head>
<body style="margin:0;padding:0;background:{EMAIL_PAGE_BG};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{EMAIL_PAGE_BG};">
<tr><td align="center" style="padding:20px 12px;">
  <table role="presentation" class="wrap" width="680" cellpadding="0" cellspacing="0" border="0" style="width:680px;max-width:680px;background:#ffffff;border-radius:12px;">
  <tr><td class="pad content" style="padding:26px 28px 30px;font-family:{EMAIL_FONT};color:{EMAIL_FG};">
{inner}
  </td></tr>
  </table>
  <div style="font-size:12px;color:#9aa1ab;margin-top:14px;font-family:{EMAIL_FONT};">
    本邮件由智能日报生成器自动发送 · 数据来源：GitHub / 飞书
  </div>
</td></tr>
</table>
</body>
</html>
"""


HTML_TEMPLATE = Template(
    """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>{{ title }}</title>
<style>
  body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         color: #24292f; line-height: 1.7; max-width: 760px; margin: 24px auto; padding: 0 16px; }
  h1 { font-size: 22px; border-bottom: 2px solid #d0d7de; padding-bottom: 8px; }
  h2 { font-size: 18px; margin-top: 28px; color: #0969da; }
  h3 { font-size: 15px; margin-top: 18px; color: #57606a; }
  table.report { border-collapse: collapse; width: 100%; margin: 12px 0; }
  table.report th, table.report td { border: 1px solid #d0d7de; padding: 6px 10px; text-align: left; }
  table.report th { background: #f6f8fa; }
  ul { padding-left: 22px; margin: 8px 0; }
  ul > li { margin-top: 10px; line-height: 1.75; }
  ul > li:first-child { margin-top: 0; }
  ul.sub { margin: 6px 0 0; padding-left: 20px; }
  ul.sub > li { margin-top: 4px; line-height: 1.7; color: #57606a; }
  code { background: #f6f8fa; padding: 1px 5px; border-radius: 4px; font-size: 90%; }
  blockquote { margin: 12px 0; padding: 8px 14px; border-left: 4px solid #d0a000;
               background: #fff8e5; color: #6b5300; }
</style>
</head>
<body>
{{ body }}
</body>
</html>
"""
)
