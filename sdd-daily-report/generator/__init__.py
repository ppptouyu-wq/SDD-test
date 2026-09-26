"""generator —— 日报生成层。

职责（specs/design.md §2）：将原始数据组织为日报（数据整理 + Markdown 生成、模板管理）。
不负责：不做数据采集；不做 API 调用；不做推送。

对外接口（design.md §4.2）：
    generate(members, date, team_name) -> DailyReport
"""

from generator.formatter import aggregate, generate

__all__ = ["aggregate", "generate"]
