"""工作日判断。

契约来源：specs/proposal.md §3.3 边界场景（v1.4 起收窄为**显式开关**）
    "仅当显式开启'仅工作日'模式（--working-days-only）时，才在周末或配置的
     法定节假日跳过日报生成"

注意：本模块只提供判断能力，**是否调用由编排层决定**。默认情况下
`main.run()` 不调用它，所以双休日与节假日照常生成日报（design.md §6.6）。
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
