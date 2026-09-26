"""工作日判断。

契约来源：specs/proposal.md §3.3 边界场景
    "系统需要识别非工作日（周末及法定节假日），并自动跳过日报生成"
"""

from __future__ import annotations

from datetime import date

from shared.errors import NonWorkingDayError


def is_working_day(day: date, holidays: list[str] | None = None) -> bool:
    """周末或配置的法定节假日返回 False。"""
    if day.weekday() >= 5:  # 5=Saturday, 6=Sunday
        return False
    return day.isoformat() not in set(holidays or [])


def ensure_working_day(day: date, holidays: list[str] | None = None) -> None:
    """非工作日抛 NonWorkingDayError，供 main 捕获后跳过。"""
    if not is_working_day(day, holidays):
        raise NonWorkingDayError(day.isoformat())
