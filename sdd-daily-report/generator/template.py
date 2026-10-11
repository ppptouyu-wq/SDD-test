"""日报模板管理。

契约来源：specs/tasks.md Task 6
    "输出到 generator/template.py：日报模板管理"

本书未给出模板的具体样式，此处按 design.md §1 的"管道式架构"与
proposal.md §2.1 的"三段式编排（代码提交→任务进展→协作沟通）"实现。
Jinja2 用于 HTML 邮件模板（pyproject.toml 依赖之一）。
"""

from __future__ import annotations

import re
from datetime import date

from jinja2 import Template

from shared.models import DailyReport, MemberReport

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
    "去掉前导空白后以 `- ` 开头"来识别列表项，子项与顶层项都渲染成 ``<li>``。
    """
    html: list[str] = []
    in_list = False
    in_table = False

    def close_blocks() -> None:
        nonlocal in_list, in_table
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
            if in_table:
                close_blocks()
            if not in_list:
                html.append("<ul>")
                in_list = True
            html.append(f"<li>{inline(stripped[2:])}</li>")
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
  ul { padding-left: 22px; }
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
