"""展示层视图模型与读取逻辑（v1.2 需求变更新增）。

契约来源：specs/design.md §3.2（视图模型）、§4（展示层函数契约）、§6.5（展示层约束）
验收来源：specs/tasks.md Task 12、specs/proposal.md §3.1/§3.2/§3.3（v1.2 新增条目）

本模块是**纯逻辑**：只依赖 ``shared.storage`` 与标准库，不导入任何 HTTP 设施，
因此可以在不启动服务的情况下被完整单元测试（design.md §6.5(3)、ADR-004）。
这与 kb-search 项目"把逻辑抽到框架无关的 handlers.py"是同一手法。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from shared.logger import get_logger
from shared.storage import ReportStorage

logger = get_logger("webview")

# ---- status 四态（design.md §3.2 唯一定义处）----
STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
#: v1.2 之前生成的历史日报未记录 ``sources``：既不能声称"正常"，也不能误报为"全部失败"。
STATUS_UNKNOWN = "unknown"

# ---- 页面固定文案（唯一定义处：测试按此处断言，避免文案漂移）----
TEXT_SOURCE_FAILED = "数据获取失败"
TEXT_ATTENDANCE_UNAVAILABLE = "考勤数据暂不可用"
TEXT_NO_RECORD_TODAY = "今日无记录"
TEXT_NO_REPORT_FOR_DATE = "该日期没有日报记录"
TEXT_SOURCES_UNRECORDED = "未记录数据源状态"
TEXT_EMPTY_DB = "本机数据库暂无记录。等待 18:00 定时任务首次生成后即可在此查看。"

#: status → 页面徽标文案（四态，与 design.md §3.2 推导表一一对应）。
STATUS_TEXT: dict[str, str] = {
    STATUS_OK: "数据源正常",
    STATUS_PARTIAL: "部分数据源失败",
    STATUS_FAILED: "数据源全部失败",
    STATUS_UNKNOWN: TEXT_SOURCES_UNRECORDED,
}

#: 展示层默认的数据源严重度排序（仅用于稳定输出顺序，不影响判定）
_SOURCE_ORDER = ("GitHub", "飞书任务", "飞书消息", "飞书考勤")


@dataclass(frozen=True)
class SourceView:
    """单个数据源的采集结果视图。"""

    name: str
    success: bool
    #: 失败原因。v1.2 只持久化了成败布尔值（``DailyReport.sources`` 为 ``dict[str, bool]``），
    #: 未持久化原因文本，故恒为 None；页面用统一的"数据获取失败"文案标注。
    error: str | None = None


@dataclass(frozen=True)
class AttendanceView:
    """考勤视图。``attendance is None`` 在页面上的语义是"考勤数据暂不可用"。"""

    check_in: str | None
    check_out: str | None
    work_hours: float
    status: str


@dataclass(frozen=True)
class CommitView:
    """代码提交视图。"""

    repo: str
    message: str
    timestamp: str | None = None
    additions: int = 0
    deletions: int = 0
    files_changed: int = 0


@dataclass(frozen=True)
class TaskView:
    """任务进展视图。"""

    title: str
    status_from: str
    status_to: str
    updated_at: str | None = None


@dataclass(frozen=True)
class MessageView:
    """协作沟通视图（内容已按 design.md §6.2 黑名单过滤敏感关键词）。"""

    sender: str
    content: str
    timestamp: str | None = None
    chat_name: str = ""


@dataclass(frozen=True)
class MemberView:
    """成员视图。"""

    name: str
    github_username: str
    commits: list[CommitView] = field(default_factory=list)
    tasks: list[TaskView] = field(default_factory=list)
    messages: list[MessageView] = field(default_factory=list)
    #: None = "考勤数据暂不可用"（与"今日无记录"是两个维度，见 design.md §3.2）
    attendance: AttendanceView | None = None


@dataclass(frozen=True)
class ReportListItem:
    """日报列表项。"""

    date: str
    team_name: str
    generated_at: str
    member_count: int
    status: str


@dataclass(frozen=True)
class ReportDetail:
    """日报详情。"""

    list_item: ReportListItem
    members: list[MemberView]
    sources: list[SourceView]
    generated_at: str


def derive_status(sources: dict[str, Any] | None) -> str:
    """由各数据源成败推导列表项状态（design.md §3.2 唯一定义处）。

    - 非空且全为真 → ``ok``
    - 至少一真一假   → ``partial``
    - 全为假       → ``failed``
    - 为空         → ``unknown``（空表示 v1.2 之前的历史记录未记录来源，
      此时不能声称"正常"，宁可显式告警）
    """
    # sources 为空 = 元数据缺失（v1.2 之前的历史记录未记录来源），不是"全部失败"：
    # 那天可能四个数据源全部成功，只是当时没记录。把"没记录"说成"全部失败"是撒谎，
    # 所以既不能声称 ok，也不能升格成 failed（见 ADR-004 补充记录）。
    if not sources:
        return STATUS_UNKNOWN
    flags = [bool(value) for value in sources.values()]
    if all(flags):
        return STATUS_OK
    if any(flags):
        return STATUS_PARTIAL
    return STATUS_FAILED


def failed_source_names(sources: dict[str, Any] | None) -> list[str]:
    """返回采集失败的**数据源名**，供页面标注"数据获取失败"。"""
    if not sources:
        return []
    return [str(name) for name, ok in sources.items() if not bool(ok)]


def list_reports(storage: ReportStorage, limit: int = 365) -> list[ReportListItem]:
    """按日报日期倒序返回日报列表项（design.md §4）。

    ``proposal.md`` §3.3（v1.2）：某条日报的日期格式异常时，**跳过该条并记日志**，
    不影响其余记录列出 —— 一条脏数据不能让整页打不开。

    ``limit`` 在**过滤之后**才施加（``storage.list_reports()`` 刻意不带 LIMIT）：
    否则一条字典序偏大的脏日期会占掉一个名额，把合法日报挤出列表。
    """
    if limit <= 0:
        return []
    items: list[ReportListItem] = []
    for row in storage.list_reports():
        raw_date = str(row.get("date") or "")
        if not _is_iso_date(raw_date):
            logger.warning(
                "日报日期格式异常，已跳过该条",
                extra={"source": "webview", "date": raw_date},
            )
            continue
        items.append(
            ReportListItem(
                date=raw_date,
                team_name=str(row.get("team_name") or ""),
                generated_at=str(row.get("generated_at") or ""),
                member_count=int(row.get("member_count") or 0),
                status=derive_status(row.get("sources")),
            )
        )
        if len(items) >= limit:
            break
    return items


def get_report(storage: ReportStorage, day: date) -> ReportDetail | None:
    """读取指定日期的日报详情；无记录返回 None（design.md §4）。

    返回 None 时页面显示"该日期没有日报记录"（``proposal.md`` §3.1 v1.2），
    而不是空白页。
    """
    row = storage.get(day)
    if row is None:
        return None

    payload = row.get("payload")
    payload = payload if isinstance(payload, dict) else {}

    raw_sources = payload.get("sources")
    sources_map: dict[str, Any] = raw_sources if isinstance(raw_sources, dict) else {}

    members = [_member_view(raw) for raw in _dicts(payload.get("members"))]
    generated_at = str(row.get("generated_at") or "")

    list_item = ReportListItem(
        date=str(row.get("date") or day.isoformat()),
        team_name=str(row.get("team_name") or ""),
        generated_at=generated_at,
        member_count=len(members),
        status=derive_status(sources_map),
    )
    return ReportDetail(
        list_item=list_item,
        members=members,
        sources=_source_views(sources_map),
        generated_at=generated_at,
    )


# ------------------------------------------------------------------ 内部工具


def _is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _dicts(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _source_views(sources_map: dict[str, Any]) -> list[SourceView]:
    """把 ``{数据源名: 是否成功}`` 转成视图列表，并按已知数据源顺序稳定排序。"""
    names = sorted(
        sources_map,
        key=lambda name: (
            _SOURCE_ORDER.index(name) if name in _SOURCE_ORDER else len(_SOURCE_ORDER),
            str(name),
        ),
    )
    return [SourceView(name=str(name), success=bool(sources_map[name])) for name in names]


def _attendance_view(raw: Any) -> AttendanceView | None:
    if not isinstance(raw, dict):
        return None
    return AttendanceView(
        check_in=_opt_str(raw.get("check_in")),
        check_out=_opt_str(raw.get("check_out")),
        work_hours=float(raw.get("work_hours") or 0.0),
        status=str(raw.get("status") or ""),
    )


def _commit_view(raw: dict) -> CommitView:
    return CommitView(
        repo=str(raw.get("repo") or ""),
        message=str(raw.get("message") or ""),
        timestamp=_opt_str(raw.get("timestamp")),
        additions=int(raw.get("additions") or 0),
        deletions=int(raw.get("deletions") or 0),
        files_changed=int(raw.get("files_changed") or 0),
    )


def _task_view(raw: dict) -> TaskView:
    return TaskView(
        title=str(raw.get("title") or ""),
        status_from=str(raw.get("status_from") or ""),
        status_to=str(raw.get("status_to") or ""),
        updated_at=_opt_str(raw.get("updated_at")),
    )


def _message_view(raw: dict) -> MessageView:
    return MessageView(
        sender=str(raw.get("sender") or ""),
        content=str(raw.get("content") or ""),
        timestamp=_opt_str(raw.get("timestamp")),
        chat_name=str(raw.get("chat_name") or ""),
    )


def _member_view(raw: dict) -> MemberView:
    return MemberView(
        name=str(raw.get("name") or ""),
        github_username=str(raw.get("github_username") or ""),
        commits=[_commit_view(item) for item in _dicts(raw.get("commits"))],
        tasks=[_task_view(item) for item in _dicts(raw.get("tasks"))],
        messages=[_message_view(item) for item in _dicts(raw.get("messages"))],
        attendance=_attendance_view(raw.get("attendance")),
    )


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


def to_jsonable(obj: Any) -> Any:
    """把视图模型转成可 JSON 序列化的结构（供 app.py 复用）。"""
    if hasattr(obj, "__dataclass_fields__"):
        return {name: to_jsonable(getattr(obj, name)) for name in obj.__dataclass_fields__}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(item) for item in obj]
    if isinstance(obj, dict):
        return {str(key): to_jsonable(value) for key, value in obj.items()}
    return obj


def iter_view_fields(obj: Any) -> Iterable[str]:
    """返回 dataclass 字段名（测试与序列化共用）。"""
    return obj.__dataclass_fields__.keys()
