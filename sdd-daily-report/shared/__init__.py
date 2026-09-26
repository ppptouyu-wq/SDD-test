"""shared —— 配置、日志、错误处理、存储（跨模块共享能力）。

契约来源：specs/design.md §2 模块职责定义
"""

from shared.models import (
    AttendanceRecord,
    CollectResult,
    CommitRecord,
    DailyReport,
    MemberReport,
    MessageRecord,
    TaskRecord,
)

__all__ = [
    "AttendanceRecord",
    "CollectResult",
    "CommitRecord",
    "DailyReport",
    "MemberReport",
    "MessageRecord",
    "TaskRecord",
]
