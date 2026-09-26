"""条件路由模式（第 8 章 8.5）：规范定义的任务分发。

书中要点：
  - 8.5.1 模式简介：路由器根据**规范**而非猜测把任务分发给对应执行者。
  - 8.5.2 SDD 为路由提供分发规则：`design.md` 的模块边界**就是**路由表 ——
    任务产出哪个文件，就路由给负责那个模块的执行者（书图 8-11）。

关键设计：路由表不硬编码，而是**从 specs/design.md 的模块职责定义中解析**。
规范改了，路由自动跟着改 —— 这就是"规范驱动"在 Agent 层的体现。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DESIGN = Path(__file__).resolve().parent.parent / "specs" / "design.md"

# 兜底模块：无法归类时的去处（例如编排入口与测试）
FALLBACK_MODULE = "main"

# design.md 模块职责表中出现、但不是代码模块的行
NON_CODE_ROWS = {"模块", "module", "---"}


@dataclass
class RouteDecision:
    """一次路由决策及其依据。"""

    task_id: str
    module: str
    handler: str
    reason: str
    files: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return f"{self.task_id} → {self.handler}（{self.reason}）"


@dataclass
class RouteTable:
    """路由表：模块 → 执行者。"""

    module_to_handler: dict[str, str]
    fallback: str = FALLBACK_MODULE

    def handlers(self) -> list[str]:
        return sorted(set(self.module_to_handler.values()) | {self.fallback})


def _strip_markdown(cell: str) -> str:
    """把表格单元格里的 Markdown 标记清掉，保留纯文本。"""
    cell = cell.replace("<br>", " ").replace("<br/>", " ")
    cell = re.sub(r"`([^`]*)`", r"\1", cell)  # 去掉行内代码反引号
    return cell.strip()


def parse_module_table(design_path: str | Path = DEFAULT_DESIGN) -> dict[str, str]:
    """从 design.md § 2 模块职责表解析 {模块名: 职责}。

    实现要点：只认**表头为「模块 / 职责」的那张表**，并在表格结束时停止。
    否则会把文末接口契约等其它表格的行也当成模块（早期版本就误把
    `GitHub API` 当成模块解析成了 `GitHub/`）。
    """
    text = Path(design_path).read_text(encoding="utf-8")
    modules: dict[str, str] = {}

    in_target_table = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_target_table:
                break  # 表格结束
            continue

        cells = [_strip_markdown(c) for c in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue

        header = cells[0].strip()
        if header in ("模块", "module", "Module"):
            in_target_table = True
            continue
        if set(header) <= set("-: "):  # 表头分隔行
            continue
        if not in_target_table:
            continue

        raw_module, duty = cells[0], cells[1]
        if raw_module.lower() in NON_CODE_ROWS:
            continue

        # 单元格可能是 "collector/ github.py lark_task.py ..."，取第一段作为模块名，
        # 且必须是**合法 Python 标识符**（"GitHub API" 之类会被此规则挡掉）
        parts = raw_module.split()
        if not parts:
            continue
        name = parts[0].strip().rstrip("/")
        name = name.removesuffix(".py")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            continue
        modules[name] = duty
    return modules


def build_route_table(design_path: str | Path = DEFAULT_DESIGN) -> RouteTable:
    """模块 → 执行者：一对一，模块名即执行者名（书图 8-11 的直译）。"""
    modules = parse_module_table(design_path)
    return RouteTable(module_to_handler={m: f"{m}-agent" for m in modules})


def module_of_file(file_path: str) -> str | None:
    """由文件路径推出所属模块：`collector/github.py` → `collector`。"""
    normalized = file_path.replace("\\", "/").strip()
    if not normalized:
        return None
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if not parts:
        return None
    if len(parts) == 1:
        # 根目录下的单文件，如 main.py
        return parts[0].removesuffix(".py")
    return parts[0]


def route_task(
    task_id: str,
    outputs: list[str],
    *,
    table: RouteTable | None = None,
) -> RouteDecision:
    """按任务产出文件把任务路由给对应模块的执行者。

    规则（来自 design.md §2 的职责边界）：
      1. 产出文件唯一时，按该文件所属模块路由；
      2. 产出跨多个模块时，路由给"职责包含编排"的 main；
      3. 无法从文件判定时回落到 main。
    """
    route_table = table or build_route_table()
    modules = {module_of_file(f) for f in outputs}
    modules.discard(None)
    known = sorted(m for m in modules if m in route_table.module_to_handler)

    if len(known) == 1:
        module = known[0]
        return RouteDecision(
            task_id=task_id,
            module=module,
            handler=route_table.module_to_handler[module],
            reason=f"产出文件全部属于模块 {module}/（design.md §2 模块边界）",
            files=list(outputs),
        )

    if len(known) > 1:
        return RouteDecision(
            task_id=task_id,
            module=route_table.fallback,
            handler=route_table.fallback + "-agent",
            reason=f"产出跨 {len(known)} 个模块（{', '.join(known)}），按编排入口路由",
            files=list(outputs),
        )

    return RouteDecision(
        task_id=task_id,
        module=route_table.fallback,
        handler=route_table.fallback + "-agent",
        reason="无法从产出文件判定模块，回落到编排入口",
        files=list(outputs),
    )


def route_all(tasks, *, table: RouteTable | None = None) -> list[RouteDecision]:
    """对一批任务批量路由（tasks 需含 task_id 与 outputs 属性）。"""
    route_table = table or build_route_table()
    return [
        route_task(t.task_id, list(t.outputs), table=route_table)
        for t in tasks
    ]


def group_by_handler(decisions: list[RouteDecision]) -> dict[str, list[RouteDecision]]:
    """按执行者分组 —— 同一执行者的任务可并行，不同执行者的任务互不干扰。"""
    grouped: dict[str, list[RouteDecision]] = {}
    for decision in decisions:
        grouped.setdefault(decision.handler, []).append(decision)
    return grouped
