"""展示层（v1.2 需求变更新增）。

契约来源：
- specs/design.md §1（只读旁路）、§2（模块职责）、§3.2（视图模型）、§4（函数契约）、§6.5（约束）
- specs/tasks.md Task 12
- specs/contracts/api-spec.yaml 的 paths（三个只读端点）
边界决策：specs/adrs/004-展示层技术选型.md（ADR-004）

分层：
- ``views.py`` —— 纯逻辑：读库 → 视图模型 → 状态判定，不依赖任何 HTTP 设施，可直接单测
- ``app.py``   —— HTTP 翻译层：把请求映射到 views 调用，本身不含业务判断
- ``static/``  —— 静态页面与样式（设计令牌取自 OpenDesign 的 ant 设计系统）

只读约束（design.md §6.5(1)）：本包对 ``data/reports.db`` 只执行查询，
不新增表、不更新字段、不删记录，也不触发生成与推送。
"""

from __future__ import annotations

__all__ = ["views", "app"]
