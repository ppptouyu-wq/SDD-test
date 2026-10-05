"""展示层（webview）测试 —— v1.2 需求变更新增。

契约来源：
    specs/design.md §3.2（status 四态推导）、§4（展示层函数契约）、§6.5（展示层约束）
    specs/tasks.md Task 12（13 条验收标准）
    specs/proposal.md §3.1/§3.2/§3.3（v1.2 新增条目）
    specs/adrs/004-展示层技术选型.md（零新增依赖、ant 令牌）

其中三条验收标准**只能靠测试守住**（运行时看不出来，只能靠断言）：
    只读连接写不进去 / 绑定 0.0.0.0 拒绝启动 / 静态资源路径穿越被拒。
"""

from __future__ import annotations

import http.client
import json
import re
import shutil
import sqlite3
import subprocess
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from shared.errors import StorageError
from shared.models import (
    AttendanceRecord,
    CommitRecord,
    DailyReport,
    MemberReport,
    MessageRecord,
    TaskRecord,
)
from shared.storage import ReportStorage

from webview import app as webview_app
from webview import views

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "webview" / "static"

#: node 不是本项目的依赖，只用来在可用时**真实执行**页面里的格式化逻辑
#: （见 test_static_page_converts_absolute_timestamps_to_the_system_timezone）。
NODE = shutil.which("node")

#: 交给 node 的探针：把 index.html 的"展示格式化"区块切出来求值，对每个入参打印
#: ``timeOf<TAB>clockOf``。用 node 而不是在 Python 里复写一遍，是因为 node 与浏览器
#: 一样读操作系统的时区设置，能真正验证"换算成当前系统时区"这件事。
_NODE_PROBE = """
const fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf8");
const start = html.indexOf("/* ---- \u5c55\u793a\u683c\u5f0f\u5316 ---- */");
const end = html.indexOf("/* ---- \u7ec4\u4ef6\uff1a\u72b6\u6001\u5fbd\u6807 ---- */");
if (start < 0 || end <= start) { console.error("格式化区块定位失败"); process.exit(2); }
const TEXT = { dash: "\\u2014", unitFiles: " \\u6587\\u4ef6", unitMembers: " \\u540d\\u6210\\u5458" };
const build = new Function("TEXT", "WEEKDAYS", html.slice(start, end) +
  "\\nreturn { timeOf: timeOf, clockOf: clockOf, stampOf: stampOf, memberMeta: memberMeta };");
const f = build(TEXT, []);
for (const value of process.argv.slice(3)) {
  console.log(f.timeOf(value) + "\\t" + f.clockOf(value) + "\\t" + f.stampOf(value));
}
"""

#: 四个数据源的**持久化键名**（DailyReport.sources 的键，见 data-models.md）
ALL_SOURCES = ("github", "lark_task", "lark_msg", "lark_attendance")


# ------------------------------------------------------------------ 夹具

def _report(
    day: date,
    *,
    sources: dict[str, bool] | None = None,
    attendance: bool = True,
) -> DailyReport:
    """构造一份用于展示层测试的日报（含成员四段：提交/任务/消息/考勤）。"""
    members = [
        MemberReport(
            name="张三",
            github_username="zhangsan",
            commits=[
                CommitRecord(
                    author="zhangsan",
                    message="feat: 日报展示页",
                    timestamp=datetime(2026, 9, 30, 10, 0),
                    repo="acme/daily-report",
                    additions=120,
                    deletions=8,
                    files_changed=4,
                )
            ],
            tasks=[
                TaskRecord(
                    assignee="张三",
                    title="展示层接口联调",
                    status_from="进行中",
                    status_to="已完成",
                    updated_at=datetime(2026, 9, 30, 18, 0),
                )
            ],
            messages=[
                MessageRecord(
                    sender="张三",
                    content="展示页联调完成，等评审",
                    timestamp=datetime(2026, 9, 30, 17, 30),
                    chat_name="项目群",
                )
            ],
            attendance=(
                AttendanceRecord(
                    employee_id="e_zhangsan",
                    date=day,
                    check_in=datetime(2026, 9, 30, 9, 2),
                    check_out=datetime(2026, 9, 30, 18, 31),
                    work_hours=8.5,
                    status="正常",
                )
                if attendance
                else None
            ),
        ),
        # 第二个人刻意什么记录都没有 —— 用来验证"今日无记录"这一段
        MemberReport(name="王五", github_username="wangwu"),
    ]
    return DailyReport(
        date=day,
        team_name="平台研发组",
        members=members,
        generated_at=datetime(2026, 9, 30, 18, 5),
        markdown="# 平台研发组 2026-09-30\n",
        html="<h1>平台研发组</h1>",
        sources=dict(sources or {}),
    )


@pytest.fixture
def db_path(tmp_dir: Path) -> Path:
    """一份包含四态各一条的日报库（可写阶段建库，之后由测试以只读打开）。"""
    path = tmp_dir / "reports.db"
    with ReportStorage(path) as store:
        # 2026-09-30 四源全成功 → ok
        store.save(_report(date(2026, 9, 30), sources={name: True for name in ALL_SOURCES}))
        # 2026-09-29 飞书任务失败、其余成功 → partial；且该成员没有考勤
        store.save(
            _report(
                date(2026, 9, 29),
                sources={"github": True, "lark_task": False, "lark_msg": True, "lark_attendance": True},
                attendance=False,
            )
        )
        # 2026-09-28 四源全失败 → failed
        store.save(_report(date(2026, 9, 28), sources={name: False for name in ALL_SOURCES}))
        # 2026-09-25 未记录 sources（v1.2 之前的历史记录）→ unknown
        store.save(_report(date(2026, 9, 25)))
    return path


@pytest.fixture
def ro(db_path: Path):
    """以只读模式打开日报库（展示层的唯一打开方式，design.md §6.5(1)）。"""
    store = ReportStorage(db_path, readonly=True)
    try:
        yield store
    finally:
        store.close()


@pytest.fixture
def server(db_path: Path):
    """真实监听回环地址的展示页服务（端口交给系统分配，避免占用 8000）。

    注意：SQLite 连接有**线程亲和性**，所以存储必须在服务的那个线程里创建
    （``ViewServer`` 的 docstring 也点名了这一点，见 ADR-004）。
    """
    ready = threading.Event()
    holder: dict = {}

    def run() -> None:
        storage = ReportStorage(db_path, readonly=True)
        httpd = webview_app.ViewServer(("127.0.0.1", 0), storage)
        holder["httpd"] = httpd
        ready.set()
        try:
            httpd.serve_forever()
        finally:
            httpd.server_close()
            storage.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(timeout=10), "展示页服务未能在 10 秒内就绪"
    try:
        yield holder["httpd"]
    finally:
        holder["httpd"].shutdown()
        thread.join(timeout=10)


def _request(server, method: str, path: str) -> tuple[int, str, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        conn.request(method, path)
        response = conn.getresponse()
        return response.status, response.getheader("Content-Type") or "", response.read()
    finally:
        conn.close()


def _static(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


# ================================================ Task 12-1：分层与零新增依赖

def test_views_layer_is_free_of_http_and_web_frameworks():
    """views.py 必须是纯逻辑：不 import 任何 HTTP 设施或 Web 框架。

    这样逻辑层可以脱离服务被完整单测（ADR-004 决策一）。
    """
    source = (ROOT / "webview" / "views.py").read_text(encoding="utf-8")
    for token in (
        "http.server",
        "BaseHTTPRequestHandler",
        "socketserver",
        "socket",
        "wsgiref",
        "flask",
        "fastapi",
        "django",
        "uvicorn",
    ):
        assert token not in source, f"views.py 不得依赖 {token}（design.md §6.5(3)）"


def test_static_layer_contains_only_html_and_css():
    """静态资源只有 index.html + app.css，没有构建步骤、没有额外 JS 文件。"""
    files = sorted(item.name for item in STATIC_DIR.iterdir() if item.is_file())
    assert files == ["app.css", "index.html"]


# ================================================ Task 12-2：只读（只能靠测试守）

def test_readonly_storage_rejects_writes(ro):
    """只读连接上的写入必须在连接层失败，而不是靠调用方自觉。"""
    with pytest.raises(StorageError):
        ro.save(_report(date(2026, 10, 1)))


def test_readonly_storage_refuses_to_create_a_new_database(tmp_dir: Path):
    missing = tmp_dir / "nope.db"
    with pytest.raises(StorageError, match="只读模式不会创建新库"):
        ReportStorage(missing, readonly=True)
    assert not missing.exists(), "只读模式不得留下任何文件"


def test_write_http_methods_are_rejected_with_405(server):
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        status, content_type, body = _request(server, method, "/api/reports")
        assert status == 405, f"{method} 应被拒绝"
        assert "只读" in json.loads(body.decode("utf-8"))["error"]


# ================================================ Task 12-3：绑定回环地址（只能靠测试守）

def test_serve_refuses_non_loopback_host(capsys):
    """绑定 0.0.0.0 必须在监听之前就被拒绝，返回退出码 2。"""
    assert webview_app.serve(db_path="data/reports.db", host="0.0.0.0", port=0) == 2
    assert "拒绝启动" in capsys.readouterr().out


def test_loopback_host_whitelist_is_exact():
    assert webview_app.LOOPBACK_HOSTS == ("127.0.0.1", "localhost", "::1")


def test_serve_reports_missing_database_instead_of_crashing(tmp_dir: Path, capsys):
    assert webview_app.serve(db_path=str(tmp_dir / "nope.db"), host="127.0.0.1", port=0) == 2
    assert "无法打开日报库" in capsys.readouterr().out


# ================================================ Task 12-4：status 四态

@pytest.mark.parametrize(
    ("sources", "expected"),
    [
        ({"github": True, "lark_task": True, "lark_msg": True, "lark_attendance": True}, "ok"),
        ({"github": True, "lark_task": False}, "partial"),
        ({"github": False, "lark_task": False}, "failed"),
        ({}, "unknown"),
        (None, "unknown"),
    ],
)
def test_derive_status_four_states(sources, expected):
    assert views.derive_status(sources) == expected


def test_empty_sources_is_unknown_and_never_reported_as_failed():
    """v1.2 之前的日报没记录来源 —— 说成"全部失败"是撒谎（ADR-004 补充记录）。"""
    assert views.derive_status({}) == views.STATUS_UNKNOWN
    assert views.derive_status({}) != views.STATUS_FAILED
    # 未记录与全失败必须是两个不同的页面标识
    assert views.STATUS_TEXT[views.STATUS_UNKNOWN] == views.TEXT_SOURCES_UNRECORDED
    assert views.STATUS_TEXT[views.STATUS_UNKNOWN] != views.STATUS_TEXT[views.STATUS_FAILED]


def test_status_text_covers_every_status():
    assert set(views.STATUS_TEXT) == {
        views.STATUS_OK,
        views.STATUS_PARTIAL,
        views.STATUS_FAILED,
        views.STATUS_UNKNOWN,
    }


def test_failed_source_names_lists_only_failures():
    assert views.failed_source_names({"github": True, "lark_task": False}) == ["lark_task"]
    assert views.failed_source_names({}) == []


# ================================================ Task 12-5：列表页

def test_list_reports_is_date_descending_and_carries_status(ro):
    items = views.list_reports(ro)

    assert [item.date for item in items] == ["2026-09-30", "2026-09-29", "2026-09-28", "2026-09-25"]
    assert [item.status for item in items] == [
        views.STATUS_OK,
        views.STATUS_PARTIAL,
        views.STATUS_FAILED,
        views.STATUS_UNKNOWN,
    ]
    assert all(item.team_name == "平台研发组" for item in items)
    assert [item.member_count for item in items] == [2, 2, 2, 2]
    assert all(item.generated_at for item in items)


def test_list_reports_skips_malformed_dates_without_breaking_the_page(db_path: Path):
    """一条脏日期不能让整页打不开（proposal.md §3.3 v1.2）。"""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO daily_reports VALUES (?, ?, ?, ?, ?, ?)",
        ("2026-13-45", "脏数据", "2026-13-45T00:00:00", "", "", json.dumps({"members": [], "sources": {}})),
    )
    conn.commit()
    conn.close()

    with ReportStorage(db_path, readonly=True) as store:
        items = views.list_reports(store)

    assert [item.date for item in items] == ["2026-09-30", "2026-09-29", "2026-09-28", "2026-09-25"]


def test_list_reports_limit_applies_after_date_filtering(db_path: Path):
    """脏日期不得占掉 limit 的名额（否则合法日报会被挤出列表）。"""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO daily_reports VALUES (?, ?, ?, ?, ?, ?)",
        # 字典序最大，排在倒序列表的第一位
        ("2026-99-99", "脏数据", "2026-99-99T00:00:00", "", "", json.dumps({"members": [], "sources": {}})),
    )
    conn.commit()
    conn.close()

    with ReportStorage(db_path, readonly=True) as store:
        items = views.list_reports(store, limit=2)

    assert [item.date for item in items] == ["2026-09-30", "2026-09-29"]


def test_list_reports_zero_limit_returns_nothing(ro):
    assert views.list_reports(ro, limit=0) == []


def test_empty_database_lists_nothing(tmp_dir: Path):
    path = tmp_dir / "empty.db"
    ReportStorage(path).close()
    with ReportStorage(path, readonly=True) as store:
        assert views.list_reports(store) == []


# ================================================ Task 12-6：详情页

def test_get_report_returns_sources_members_and_generated_at(ro):
    detail = views.get_report(ro, date(2026, 9, 30))

    assert detail is not None
    assert detail.list_item.date == "2026-09-30"
    assert detail.list_item.status == views.STATUS_OK
    assert detail.generated_at == "2026-09-30T18:05:00"
    assert [source.name for source in detail.sources] == sorted(ALL_SOURCES)
    assert all(source.success for source in detail.sources)


def test_get_report_marks_failed_sources(ro):
    detail = views.get_report(ro, date(2026, 9, 29))

    assert detail is not None
    assert detail.list_item.status == views.STATUS_PARTIAL
    assert [source.name for source in detail.sources if not source.success] == ["lark_task"]


def test_get_report_returns_none_for_a_date_without_record(ro):
    """没有记录要显示"该日期没有日报记录"，不是空白页。"""
    assert views.get_report(ro, date(2026, 1, 1)) is None


def test_member_carries_the_four_fixed_sections(ro):
    detail = views.get_report(ro, date(2026, 9, 30))
    assert detail is not None

    member = detail.members[0]
    assert member.name == "张三"
    assert member.github_username == "zhangsan"
    assert member.commits[0].repo == "acme/daily-report"
    assert member.commits[0].additions == 120
    assert member.tasks[0].title == "展示层接口联调"
    assert member.tasks[0].status_to == "已完成"
    assert member.messages[0].sender == "张三"
    assert member.messages[0].chat_name == "项目群"
    assert member.attendance is not None
    assert member.attendance.work_hours == 8.5


def test_time_fields_reach_the_page_as_full_iso_strings(ro):
    """时间字段一律以完整 ISO 字符串交给页面（data-models.md §3 的约定）。

    回归测试：v1.2 首版的 ``clockOf()`` 以为拿到的是 ``"09:02"``，于是把
    ``2026-09-30T09:02:00`` 原样吐到页面上（实测截图发现签到/签退两列显示成
    带 ``T`` 和时区偏移的长串）。契约这一侧此前完全没断言，所以测试没拦住它 ——
    现在把"视图层不截断、由页面渲染时格式化"这条约定钉住。
    """
    detail = views.get_report(ro, date(2026, 9, 30))
    assert detail is not None

    member = detail.members[0]
    assert member.attendance is not None
    assert member.attendance.check_in == "2026-09-30T09:02:00"
    assert member.attendance.check_out == "2026-09-30T18:31:00"
    # 同一条日报里其它时间字段同样是 ISO，页面按同一套方式格式化。
    assert member.commits[0].timestamp == "2026-09-30T10:00:00"
    assert member.tasks[0].updated_at == "2026-09-30T18:00:00"


def test_absolute_timestamps_keep_their_offset_on_the_wire(tmp_dir: Path):
    """带时区偏移的时间必须**原样**送到页面，视图层不负责换算。

    回归测试：GitHub 采集器产出的是 ``+00:00``（UTC）、飞书考勤是 ``+08:00``，
    两者混在同一张卡片上。页面此前靠 ``value.slice(11, 16)`` 取值，于是 UTC 的
    06:38 被当成本地时间显示，比真实时间早 8 小时。修法是在**页面**里做换算，
    所以这一侧必须钉住"偏移不许丢"——一旦视图层擅自本地化或去掉偏移，
    页面再换算一次就会错上两倍偏移。
    """
    path = tmp_dir / "aware.db"
    report = _report(date(2026, 10, 5))
    member = report.members[0]
    member.commits[0].timestamp = datetime(2026, 10, 5, 6, 38, 17, tzinfo=timezone.utc)
    assert member.attendance is not None
    member.attendance.check_in = datetime(2026, 4, 24, 9, 32, tzinfo=timezone(timedelta(hours=8)))

    with ReportStorage(path) as store:
        store.save(report)
    with ReportStorage(path, readonly=True) as store:
        detail = views.get_report(store, date(2026, 10, 5))

    assert detail is not None
    read = detail.members[0]
    assert read.commits[0].timestamp == "2026-10-05T06:38:17+00:00"
    assert read.attendance is not None
    assert read.attendance.check_in == "2026-04-24T09:32:00+08:00"


def test_static_page_timezone_normalization_is_wired_into_every_clock():
    """页面的时间一律经统一的换算函数渲染，不得再靠字符串截取。

    回归测试：``timeOf()`` 原先是 ``value.slice(11, 16)``，对 GitHub 的 UTC 串
    （``2026-10-05T06:38:17+00:00``）会显示成 ``06:38``，而同一张卡片上的考勤
    是 ``+08:00``（显示正确）——同一份日报里时间自相矛盾。
    """
    html = _static("index.html")

    for name in ("offsetMinutes", "stampParts", "stampOf", "pad2"):
        assert re.search(r"function\s+%s\s*\(" % name, html), f"index.html 缺少 {name}()"
    assert "Date.UTC(" in html, "缺少绝对时刻运算（Date.UTC），无法按偏移换算"
    assert "(Z|[+-]" in html, "STAMP 正则没有识别时区偏移（Z / ±HH:MM）"

    assert "value.slice(11, 16)" not in html, "timeOf 又退回了「截掉日期时区」的旧写法"

    body = re.search(r"function\s+timeOf\s*\(value\)\s*\{(.*?)\n  \}", html, re.DOTALL)
    assert body is not None, "index.html 里找不到 timeOf()"
    assert "stampParts(value)" in body.group(1), "timeOf() 没有走统一换算"

    clock = re.search(r"function\s+clockOf\s*\(value\)\s*\{(.*?)\n  \}", html, re.DOTALL)
    assert clock is not None, "index.html 里找不到 clockOf()"
    assert "return timeOf(value);" in clock.group(1), (
        "clockOf() 没有复用 timeOf() —— 考勤会与其它时间字段用两套口径"
    )

    # 列表行与详情总览共用 memberMeta：只给 HH:MM 会让人误以为是当天凌晨
    meta = re.search(r"function\s+memberMeta\s*\(item\)\s*\{(.*?)\n  \}", html, re.DOTALL)
    assert meta is not None, "index.html 里找不到 memberMeta()"
    assert "stampOf(" in meta.group(1), "生成时间没有走带日期的 stampOf()"


@pytest.mark.skipif(NODE is None, reason="需要 node 才能真实执行页面里的格式化逻辑")
def test_static_page_converts_absolute_timestamps_to_the_system_timezone(tmp_dir: Path):
    """真实跑一遍页面逻辑：带偏移的时间换算成**本机时区**，不带偏移的按本地墙上时间原样显示。

    Python 的 ``astimezone()`` 和 node 读的是同一套操作系统时区设置，所以期望值在
    任何时区的机器上都成立 —— 测试不写死 ``+08:00``。
    """
    probe = tmp_dir / "probe.js"
    probe.write_text(_NODE_PROBE, encoding="utf-8")

    absolute = [
        "2026-10-05T06:38:17+00:00",   # GitHub 提交（真实库里那条，曾显示成 06:38）
        "2026-10-05T06:38:17Z",        # 同上的 Z 写法
        "2026-04-24T09:32:00+08:00",   # 飞书考勤（本来就对，不能改坏）
        "2026-10-05T06:38:17-05:00",   # 负偏移
    ]
    wall = ["2026-10-05T14:40:49.683196", "2026-09-30T10:00:00"]  # generated_at 等本地墙上时间
    samples = absolute + wall

    result = subprocess.run(
        [NODE, str(probe), str(STATIC_DIR / "index.html"), *samples],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"node 探针失败：{result.stderr}"

    lines = result.stdout.strip().splitlines()
    assert len(lines) == len(samples), f"探针输出行数不对：{result.stdout!r}"

    for value, line in zip(samples, lines):
        shown, clocked, stamped = line.split("\t")
        if value in absolute:
            local = datetime.fromisoformat(value).astimezone()
            want = local.strftime("%H:%M")
            assert shown == want, f"timeOf({value}) 应为本机时间 {want}，实际 {shown}"
            assert clocked == want, f"clockOf({value}) 应为本机时间 {want}，实际 {clocked}"
            want_stamp = local.strftime("%Y-%m-%d %H:%M")
        else:
            want = value[11:16]
            assert shown == want, f"不带偏移的 {value} 是本地墙上时间，应原样显示 {want}，实际 {shown}"
            want_stamp = value[:10] + " " + want
        assert stamped == want_stamp, f"stampOf({value}) 应为 {want_stamp}，实际 {stamped}"


def test_static_page_does_not_steal_focus_on_first_paint():
    """首次渲染不得移动焦点。

    回归测试：v1.2 首版在 ``renderRoute()`` 首次执行时就 ``focus()`` 了标题，
    ``:focus-visible`` 于是在 ``h1`` 上画了一圈品牌红焦点环（design.md §6.5(4)
    把 ``--accent`` 定义成"标题重音 + 焦点环"）。本页配色里红色只代表错误，
    打开页面就看到一个红框会被误读成报错。
    """
    html = _static("index.html")
    assert "viewRenderedOnce" in html, "缺少首帧守卫变量"
    body = re.search(r"function\s+setFocus\s*\(activeView\)\s*\{(.*?)\n  \}", html, re.DOTALL)
    assert body is not None, "index.html 里找不到 setFocus()"
    assert "if (!viewRenderedOnce) return;" in body.group(1), (
        "setFocus() 没有首帧守卫 —— 页面刚打开就会在标题上画出焦点环"
    )


def test_detail_view_heading_id_matches_its_aria_labelledby():
    """``<section id="view-detail" aria-labelledby="h-detail">`` 引用的 id 必须真实存在。

    v1.2 首版的详情标题没有 id，``aria-labelledby`` 悬空 —— 该视图没有无障碍名称，
    而且 ``setFocus()`` 也找不到焦点目标。
    """
    html = _static("index.html")
    assert 'aria-labelledby="h-detail"' in html
    assert 'title.id = "h-detail"' in html, "详情标题没有设置 id=\"h-detail\""
    assert 'title.setAttribute("data-focus", "")' in html, "详情标题不是焦点目标"


def test_missing_attendance_is_none_not_an_empty_record(ro):
    """考勤缺失 → None，语义是"考勤数据暂不可用"，与"今日无记录"是两个维度。"""
    detail = views.get_report(ro, date(2026, 9, 29))
    assert detail is not None
    assert detail.members[0].attendance is None
    assert detail.members[1].attendance is None


def test_member_with_no_records_keeps_empty_lists(ro):
    """王五没有任何记录 —— 四段都必须在，只是列表为空（页面显示"今日无记录"）。"""
    detail = views.get_report(ro, date(2026, 9, 30))
    assert detail is not None

    second = detail.members[1]
    assert second.name == "王五"
    assert second.commits == []
    assert second.tasks == []
    assert second.messages == []


def test_to_jsonable_produces_json(ro):
    detail = views.get_report(ro, date(2026, 9, 30))
    payload = views.to_jsonable(detail)
    assert json.loads(json.dumps(payload, ensure_ascii=False))["list_item"]["status"] == views.STATUS_OK
    assert list(views.iter_view_fields(detail.list_item)) == [
        "date",
        "team_name",
        "generated_at",
        "member_count",
        "status",
    ]


# ================================================ Task 12-7：HTTP 接口

def test_index_and_stylesheet_are_served(server):
    status, content_type, body = _request(server, "GET", "/")
    assert status == 200
    assert "text/html" in content_type
    assert b"<html" in body.lower()

    status, content_type, _ = _request(server, "GET", "/app.css")
    assert status == 200
    assert "text/css" in content_type


def test_api_reports_lists_all_reports(server):
    status, content_type, body = _request(server, "GET", "/api/reports")
    assert status == 200
    assert "application/json" in content_type

    payload = json.loads(body.decode("utf-8"))
    assert [item["date"] for item in payload] == ["2026-09-30", "2026-09-29", "2026-09-28", "2026-09-25"]
    assert payload[-1]["status"] == views.STATUS_UNKNOWN


def test_api_report_detail_success(server):
    status, _, body = _request(server, "GET", "/api/reports/2026-09-30")
    assert status == 200

    payload = json.loads(body.decode("utf-8"))
    assert payload["list_item"]["status"] == views.STATUS_OK
    assert len(payload["members"]) == 2


def test_api_report_detail_rejects_malformed_date(server):
    status, _, body = _request(server, "GET", "/api/reports/2026-13-45")
    assert status == 400
    assert "日期格式非法" in json.loads(body.decode("utf-8"))["error"]


def test_api_report_detail_returns_404_with_a_readable_message(server):
    status, _, body = _request(server, "GET", "/api/reports/2026-01-01")
    assert status == 404
    assert json.loads(body.decode("utf-8"))["error"] == views.TEXT_NO_REPORT_FOR_DATE


def test_unknown_paths_return_404(server):
    for path in ("/nope", "/api", "/static/app.js", "/static/../main.py", "/static/../../main.py"):
        status, _, _ = _request(server, "GET", path)
        assert status == 404, f"{path} 不应被服务"


def test_server_version_identifies_the_view_layer(server):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        conn.request("GET", "/api/reports")
        assert conn.getresponse().getheader("Server").strip() == "SDDReportView/1.2"
    finally:
        conn.close()


# ================================================ Task 12-8：静态页与规范同源

def test_index_html_repeats_the_fixed_texts_verbatim():
    """页面文案必须与 views.py 的 TEXT_* 逐字一致（唯一定义处，防文案漂移）。"""
    html = _static("index.html")

    for text in (
        views.TEXT_SOURCE_FAILED,
        views.TEXT_ATTENDANCE_UNAVAILABLE,
        views.TEXT_NO_RECORD_TODAY,
        views.TEXT_NO_REPORT_FOR_DATE,
        views.TEXT_SOURCES_UNRECORDED,
        views.TEXT_EMPTY_DB,
    ):
        assert text in html, f"index.html 缺少固定文案：{text}"

    for status, label in views.STATUS_TEXT.items():
        assert label in html, f"index.html 缺少 {status} 的徽标文案：{label}"


def test_index_html_is_framework_free_and_buildless():
    html = _static("index.html")
    lowered = html.lower()

    for token in ("react", "vue", "svelte", "tailwind", "webpack", "vite", "jquery", "bootstrap"):
        assert not re.search(rf"\b{token}\b", lowered), f"静态页不得引入 {token}（ADR-004）"

    # 不得引用任何外部资源（离线可用 + 不引入供应链）
    assert not re.search(r'(?:src|href)\s*=\s*["\']https?://', lowered)

    # 不得硬编码监听地址：数据一律走相对路径的同源接口
    for token in ("0.0.0.0", "localhost", "127.0.0.1"):
        assert token not in lowered, f"静态页不得出现 {token}"
    api_calls = re.findall(r"fetch\(\s*[`'\"]([^`'\"]+)", html)
    assert api_calls, "静态页必须通过 fetch 读取真实数据（设计稿的假数据不得进产品）"
    assert all(call.startswith("/api/reports") for call in api_calls), api_calls


def test_app_css_declares_the_desktop_floor():
    css = _static("app.css")
    assert "1024px" in css, "桌面单页下限 1024px（design.md §6.5(2)）"


def test_app_css_uses_the_ant_design_tokens():
    """视觉同源：颜色全部来自 ant 的 design-tokens.json，不得自造色。"""
    css = _static("app.css")

    for value in (
        "#ffffff",
        "#f7f8fa",
        "#fff1f0",
        "#1f1f1f",
        "#4b5563",
        "#697386",
        "#d9dce3",
        "#eef0f4",
        "#d32029",
        "#22a06b",
        "#faad14",
        "#cf1322",
    ):
        assert value in css, f"app.css 缺少 ant 令牌值 {value}"

    # 身份色与错误色必须分开：失败/错误态用 --c-error，绝不复用 --c-brand
    assert re.search(r"--c-brand\s*:\s*#d32029", css)
    assert re.search(r"--c-error\s*:\s*#cf1322", css)


def test_app_css_failure_states_use_the_danger_color():
    css = _static("app.css")
    root_end = css.index("}", css.index(":root"))
    body = css[root_end:]

    assert "var(--c-error)" in body or "#cf1322" in body
    # 失败相关的类不得染上品牌红
    for line in body.splitlines():
        if "var(--c-error)" in line or "#cf1322" in line:
            assert "var(--c-brand)" not in line, f"失败态误用了品牌红：{line.strip()}"


# ================================================ Task 12-9：main.py 接线

def test_cli_exposes_serve_port_and_db_flags():
    from main import build_parser

    args = build_parser().parse_args(["--serve", "--port", "8123", "--db", "x.db"])
    assert args.serve is True
    assert args.port == 8123
    assert args.db == "x.db"

    # 不传时也不能报错（--serve 之外的所有既有用法不受影响）
    assert build_parser().parse_args([]).serve is False


def test_serve_falls_back_to_the_default_db_when_config_is_missing(tmp_dir: Path, monkeypatch):
    import main as main_mod

    captured: dict = {}
    monkeypatch.setattr(webview_app, "serve", lambda **kwargs: (captured.update(kwargs), 0)[1])

    args = main_mod.build_parser().parse_args(["--serve", "--config", str(tmp_dir / "nope.yaml")])
    assert main_mod._serve(args) == 0
    assert captured["db_path"] == str(main_mod.DEFAULT_STORAGE)
    assert captured["host"] == webview_app.DEFAULT_HOST
    assert captured["port"] == webview_app.DEFAULT_PORT


def test_serve_prefers_the_explicit_db_flag(monkeypatch):
    import main as main_mod

    captured: dict = {}
    monkeypatch.setattr(webview_app, "serve", lambda **kwargs: (captured.update(kwargs), 0)[1])

    args = main_mod.build_parser().parse_args(["--serve", "--db", "custom.db", "--port", "9100"])
    assert main_mod._serve(args) == 0
    assert captured["db_path"] == "custom.db"
    assert captured["port"] == 9100


def test_serve_never_imports_http_server_on_the_cron_path():
    """零新增依赖：`http.server` 只允许出现在 webview 包里，不得进入采集/生成链路。

    只看**真正的 import 语句**，不看字符串字面量
    （``sdd_agents/guardrail.py`` 的自检样本里引用过这行文本，那是数据不是依赖）。
    """
    pattern = re.compile(r"^[ \t]*(?:import\s+http\.server|from\s+http\.server\b)", re.MULTILINE)
    for folder in ("collector", "generator", "notifier", "shared", "sdd_agents", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert not pattern.search(text), f"{path} 不应引入展示层依赖"
