"""自定义异常 + 错误处理策略。

契约来源：specs/tasks.md Task 2 验收标准
          "errors.py 定义 CollectorError、GeneratorError、NotifierError 三个自定义异常"

配置来源：specs/design.md §6.1 错误处理策略（优雅降级）
"""

from __future__ import annotations


class DailyReportError(Exception):
    """本系统所有异常的基类。"""

    def __init__(self, message: str, *, source: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.source = source

    def __str__(self) -> str:  # pragma: no cover - trivial
        if self.source:
            return f"[{self.source}] {self.message}"
        return self.message


class ConfigError(DailyReportError):
    """配置缺失或非法。"""

    def __init__(self, message: str) -> None:
        super().__init__(message, source="config")


class CollectorError(DailyReportError):
    """采集层失败（API 超时、限流、Token 过期等）。

    对应 design.md §6.1：采集层失败不应阻塞生成层。
    """

    def __init__(self, message: str, *, source: str = "collector") -> None:
        super().__init__(message, source=source)


class GeneratorError(DailyReportError):
    """生成层失败。

    对应 design.md §6.1：生成层失败不应阻塞推送层（需要推送错误报告）。
    """

    def __init__(self, message: str, *, source: str = "generator") -> None:
        super().__init__(message, source=source)


class NotifierError(DailyReportError):
    """推送层失败。"""

    def __init__(self, message: str, *, source: str = "notifier") -> None:
        super().__init__(message, source=source)


class StorageError(DailyReportError):
    """存储层失败。"""

    def __init__(self, message: str) -> None:
        super().__init__(message, source="storage")


class NonWorkingDayError(DailyReportError):
    """非工作日（周末及法定节假日）——用于跳过日报生成。

    对应 proposal.md §3.3 边界场景："系统需要识别非工作日……并自动跳过日报生成"。
    """

    def __init__(self, day: str) -> None:
        super().__init__(f"{day} 是非工作日，跳过日报生成", source="calendar")
