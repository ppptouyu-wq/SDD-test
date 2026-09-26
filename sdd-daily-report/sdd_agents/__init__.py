"""SDD × Agent 设计模式：可运行的骨架。

书中第 8 章讲 5 种 Agent 设计模式与 SDD 的结构性融合。本项目落地其中 4 种：

| 模式 | 书中小节 | 实现文件 |
|---|---|---|
| 规划与执行 | 8.1 | `orchestrator.py`（tasks.md 即规划器产出） |
| 生成—评审 | 8.2 | `generate_review.py` |
| 层级委托 | 8.3 | `orchestrator.py` + `subagent.py` |
| 护栏三明治 | 8.4 | `guardrail.py`（含已上线的 scripts/ 护栏） |
| 条件路由 | 8.5 | `router.py` |

设计原则：**模式必须能直接作用于本项目的真实规范，而不是玩具示例**。
`generate_review` 审查的就是 `specs/proposal.md`，`router` 的路由规则就来自
`specs/design.md` 的模块职责定义。因此这些模块既是方法论落地，也是可用的工具。
"""

from sdd_agents.generate_review import ReviewFinding, review_spec
from sdd_agents.guardrail import GuardrailReport, run_guardrails
from sdd_agents.orchestrator import Orchestrator, TaskSpec, load_tasks
from sdd_agents.router import RouteDecision, route_task

__all__ = [
    "GuardrailReport",
    "Orchestrator",
    "ReviewFinding",
    "RouteDecision",
    "TaskSpec",
    "load_tasks",
    "review_spec",
    "route_task",
    "run_guardrails",
]
