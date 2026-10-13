"""数据模型定义。

契约来源：specs/design.md §3、specs/contracts/data-models.md
规范原文（书图 5-6）：只承载语义，不绑定实现。本实现选择 dataclasses（stdlib，零依赖）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Generic, TypeVar


@dataclass
class CommitRecord:
    """代码提交记录"""

    author: str  # 提交者（GitHub用户名）
    message: str  # 提交信息（Commit Message）
    timestamp: datetime  # 提交时间
    repo: str  # 仓库名称
    additions: int  # 新增行数
    deletions: int  # 删除行数
    files_changed: int  # 变更文件数


@dataclass
class TaskRecord:
    """任务变更记录"""

    assignee: str  # 负责人（飞书用户名）
    title: str  # 任务标题
    status_from: str  # 原状态
    status_to: str  # 新状态
    updated_at: datetime  # 变更时间


@dataclass
class MessageRecord:
    """消息记录"""

    sender: str  # 发送者（飞书用户名）
    content: str  # 消息内容（纯文本）
    timestamp: datetime  # 发送时间
    chat_name: str  # 群名称


@dataclass
class AttendanceRecord:
    """考勤记录（v1.1 新增，见 specs/contracts/data-models.md）"""

    employee_id: str  # 飞书员工 ID（employee_id，不是 open_id；见 design.md §3.1）
    date: date  # 考勤日期
    check_in: datetime | None  # 签到时间
    check_out: datetime | None  # 签退时间
    work_hours: float  # 工时（小时）
    status: str  # 正常 / 迟到 / 早退 / 缺勤 / 休假


@dataclass
class MemberReport:
    """成员报告"""

    name: str  # 成员姓名
    github_username: str  # GitHub用户名
    commits: list[CommitRecord] = field(default_factory=list)  # 代码提交记录
    tasks: list[TaskRecord] = field(default_factory=list)  # 任务变更记录
    messages: list[MessageRecord] = field(default_factory=list)  # 相关消息记录
    attendance: AttendanceRecord | None = None  # 考勤记录（v1.1 新增）


@dataclass
class RepoProgress:
    """当日工程进展（v1.8 新增，团队级；见 specs/design.md §3.4、§6.8）。

    取数只用两种可复现的客观事实：本地 Git 历史 + 规范覆盖率的静态统计。
    它刻意**不写入** `DailyReport.sources` —— 它不是外部协作平台，
    失败原因多为"这台机器上不是 Git 仓库"，属可解释的缺失（`proposal.md` §4「工程数据源」）。
    """

    commit_count: int  # 当日提交数（本地 Git 历史）
    version: str  # 当日最高迭代版本号，如 v1.8；无则空串
    change_layer: str  # 变更起点层：需求变更 / 设计变更 / 任务变更 / 缺陷修复 / 未识别
    spec_changed: list[str] = field(default_factory=list)  # 当日被改动的规范文件（仓库根相对路径）
    code_changed: bool = False  # 当日是否改动了代码或测试
    same_commit: bool = False  # 是否至少有一个提交同时改了规范与代码（书图 7-8 闭环）
    spec_coverage: str = ""  # 规范覆盖率文本，如 "97/98 = 99.0%"；取不到则空串


@dataclass
class DailyReport:
    """每日报告"""

    date: date  # 日报日期
    team_name: str  # 团队名称
    members: list[MemberReport]  # 各成员们的日报段落
    generated_at: datetime  # 生成时间
    markdown: str  # 完整的Markdown格式日报
    html: str  # 完整的HTML格式日报
    # 各数据源当日是否采集成功（v1.2 新增，键为数据源名如 github/lark_task/lark_msg/lark_attendance）。
    # 展示层据此推导日报状态（design.md §3.2）；默认空字典保证既有构造调用不受影响，
    # 而"空"在展示层被判定为 unknown（未记录数据源状态），不会被误报成"全部失败"。
    sources: dict[str, bool] = field(default_factory=dict)
    # 当日工程进展（v1.8 新增）。取不到仓库信息时为 None → 邮件渲染中性空态；
    # 刻意不进 sources（design.md §3.4）。
    progress: RepoProgress | None = None


T = TypeVar("T")


@dataclass
class CollectResult(Generic[T]):
    """采集层统一返回类型（ADR-003）。

    v1.0 返回 list[Record]，无法区分"今日确实没有数据"与"API 调用失败"。
    依据 proposal.md §3.2"采集失败必须显式标注、严禁静默跳过"，v1.1 改为本类型。
    """

    success: bool  # 采集是否成功
    records: list[T] = field(default_factory=list)  # 采集到的记录（失败时为空列表）
    error_message: str | None = None  # 失败原因（成功时为 None）

    @classmethod
    def ok(cls, records: list[T]) -> "CollectResult[T]":
        return cls(success=True, records=records, error_message=None)

    @classmethod
    def fail(cls, error_message: str) -> "CollectResult[T]":
        return cls(success=False, records=[], error_message=error_message)
