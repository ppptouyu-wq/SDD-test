"""层级委托模式 + 子智能体并行执行（第 8 章 8.3 / 第 6 章 6.7）。

书中要点：
  - 8.3.1 模式简介：管理者（Manager）不干活，只按规范拆任务并委派给工作者（Worker）。
  - 8.3.2 SDD 与层级委托的结合：`tasks.md` 就是管理者的任务分配表；
    每个 Worker 只拿到自己那个任务的描述与验收标准，不需要理解整个系统。
  - 6.7.2 并行执行的安全边界：只有**互不依赖**的任务才能并行；
    同一文件被多个任务改动、或存在依赖关系时必须串行。

本实现把 tasks.md 解析成 DAG，按"波次（wave）"调度：同一波次内的任务并行执行。
执行者（Worker）是可注入的 —— 真实环境里它是 LLM 子智能体，本环境下默认是
`PytestVerifier`（用真实测试作为"任务是否完成"的客观判据，无需 LLM 也能跑）。
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Protocol

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TASKS = ROOT / "specs" / "tasks.md"


@dataclass
class TaskSpec:
    """tasks.md 中的一个任务（书图 6-2 的三层结构）。"""

    task_id: str
    title: str
    description: str = ""
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return f"{self.task_id}: {self.title}"


@dataclass
class TaskResult:
    """一个任务的执行结果。"""

    task_id: str
    ok: bool
    detail: str
    duration: float = 0.0
    wave: int = 0
    worker: str = ""


class Worker(Protocol):
    """工作者接口：真实环境里由 LLM 子智能体实现。"""

    name: str

    def execute(self, task: TaskSpec) -> tuple[bool, str]:
        ...


# ------------------------------------------------------------------ 解析

_TASK_HEADING = re.compile(r"^##\s*(Task\s*\d+)\s*[:：]\s*(.+?)\s*$", re.IGNORECASE)
_FIELD = re.compile(
    r"^(描述|输入|输出|依赖|验收标准)\s*[:：]\s*(.*)$"
)


def load_tasks(path: str | Path = DEFAULT_TASKS) -> list[TaskSpec]:
    """解析 tasks.md 为 TaskSpec 列表。

    只认形如 `## Task N: 标题` 的二级标题；元信息表与执行顺序图会被忽略。
    """
    text = Path(path).read_text(encoding="utf-8")
    tasks: list[TaskSpec] = []
    current: TaskSpec | None = None
    section: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        heading = _TASK_HEADING.match(line)
        if heading:
            if current is not None:
                tasks.append(current)
            current = TaskSpec(
                task_id=heading.group(1).replace(" ", ""),
                title=heading.group(2).strip(),
            )
            section = None
            continue

        if current is None:
            continue

        # 新的一级/二级标题（如 "## 执行顺序"）意味着当前任务结束
        if line.startswith("## ") and not _TASK_HEADING.match(line):
            tasks.append(current)
            current = None
            section = None
            continue

        field_match = _FIELD.match(line)
        if field_match:
            section = field_match.group(1)
            value = field_match.group(2).strip()
            if section == "描述" and value:
                current.description = value
            elif section == "输入" and value:
                current.inputs.append(value)
            elif section == "输出" and value:
                current.outputs.extend(_split_outputs(value))
            elif section == "依赖":
                current.dependencies.extend(_split_deps(value))
            continue

        stripped = line.strip()
        if not stripped:
            continue

        if section == "描述" and not stripped.startswith("#"):
            current.description = (current.description + " " + stripped).strip()
        elif section == "输出":
            current.outputs.extend(_split_outputs(stripped))
        elif section == "依赖":
            current.dependencies.extend(_split_deps(stripped))
        elif section == "验收标准":
            checkbox = re.match(r"^-\s*\[[ x]\]\s*(.+)$", stripped)
            if checkbox:
                current.acceptance.append(checkbox.group(1).strip())
            elif stripped.startswith("-"):
                current.acceptance.append(stripped.lstrip("- ").strip())

    if current is not None:
        tasks.append(current)
    return tasks


def _split_outputs(value: str) -> list[str]:
    """输出字段的多种写法：

    - `collector/github.py+tests/test_github.py`
    - `shared/config.py、shared/logger.py、shared/errors.py、shared/storage.py`
    - `项目根目录结构+pyproject.toml+config.yaml.example`
    """
    value = value.strip().strip("`")
    parts = re.split(r"\s*\+\s*|\s*,\s*|\s*、\s*|\s*和\s*", value)
    cleaned = []
    for part in parts:
        item = part.strip().strip("`").strip()
        if not item:
            continue
        # 保留形如 a/b.py 或 x.py 的路径；跳过"项目根目录结构"这类描述
        cleaned.append(item)
    return cleaned


def _split_deps(value: str) -> list[str]:
    """依赖字段可能是 `Task 3、Task 4、Task 5`、`Task 2` 或 `无`。"""
    value = value.strip()
    if value in ("无", "None", "-", ""):
        return []
    return [f"Task{m}" for m in re.findall(r"Task\s*(\d+)", value, flags=re.IGNORECASE)]


# ------------------------------------------------------------ DAG 与调度

def build_waves(tasks: Iterable[TaskSpec]) -> list[list[TaskSpec]]:
    """按依赖关系把任务分波：同一波次内的任务互不依赖，可并行。

    对于引用了不存在任务的依赖（例如 Task 11 依赖 Task 3~5 之外的编号），
    直接忽略该依赖，避免整图卡死。
    """
    task_list = list(tasks)
    by_id = {t.task_id: t for t in task_list}
    remaining = {t.task_id: set(d for d in t.dependencies if d in by_id) for t in task_list}
    done: set[str] = set()
    waves: list[list[TaskSpec]] = []

    while remaining:
        ready = [tid for tid, deps in remaining.items() if deps <= done]
        if not ready:
            # 存在环：为可诊断性，把剩余任务一次性输出并中止
            cycle = ", ".join(sorted(remaining))
            raise ValueError(f"任务依赖存在环，无法调度：{cycle}")
        wave = [by_id[tid] for tid in sorted(ready, key=_task_order_by_id)]
        waves.append(wave)
        for tid in ready:
            done.add(tid)
            remaining.pop(tid)
    return waves


def _task_order(task: TaskSpec) -> int:
    m = re.search(r"(\d+)", task.task_id)
    return int(m.group(1)) if m else 0


@dataclass
class Conflict:
    """并行安全边界冲突（书 6.7.2）。"""

    kind: str
    detail: str


def detect_conflicts(wave: list[TaskSpec]) -> list[Conflict]:
    """检查同一波次内是否存在并行不安全的情况。

    书 6.7.2 举出的两类边界：
      - 多个任务写同一个文件 → 并行会互相覆盖
      - 任务之间存在未声明的隐式依赖（这里表现为重复产出文件）
    """
    conflicts: list[Conflict] = []
    seen: dict[str, str] = {}
    for task in wave:
        for output in task.outputs:
            key = output.replace("\\", "/").strip()
            if key in seen:
                conflicts.append(
                    Conflict(
                        kind="shared_output",
                        detail=f"{seen[key]} 与 {task.task_id} 都产出 {key}，不可并行",
                    )
                )
            else:
                seen[key] = task.task_id
    return conflicts


# ---------------------------------------------------------------- 工作者

@dataclass
class PytestVerifier:
    """默认工作者：用真实测试作为"任务完成"的客观判据。

    书中第 7.5.6 节 Step 5 的做法就是跑 `pytest tests/ -v`。
    真实环境下这里应替换为 LLM 子智能体（把任务描述 + 验收标准交给它执行）。
    """

    name: str = "pytest-verifier"
    test_paths: tuple[str, ...] = ()
    timeout: int = 300

    def execute(self, task: TaskSpec) -> tuple[bool, str]:
        # 不加 -q：-q 的汇总行格式随 pytest 版本变化，这里取带 "passed" 的常规摘要行
        cmd = [sys.executable, "-m", "pytest", *self.test_paths]
        try:
            proc = subprocess.run(
                cmd,
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return False, f"验证超时（>{self.timeout}s）"
        output = ((proc.stdout or "") + (proc.stderr or "")).strip()
        lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
        last = lines[-1] if lines else "(无输出)"
        return proc.returncode == 0, last


@dataclass
class SubagentWorker:
    """可注入的假工作者：用于测试调度逻辑本身，不跑真实测试。"""

    name: str = "subagent"
    delay: float = 0.0
    behaviour: Callable[[TaskSpec], tuple[bool, str]] | None = None
    calls: list[str] = field(default_factory=list)

    def execute(self, task: TaskSpec) -> tuple[bool, str]:
        self.calls.append(task.task_id)
        if self.delay:
            time.sleep(self.delay)
        if self.behaviour is not None:
            return self.behaviour(task)
        return True, "ok"


# ------------------------------------------------------------- 编排器

@dataclass
class Orchestrator:
    """管理者（Manager）：按 tasks.md 分波委派，不亲自干活（书 8.3）。"""

    tasks: list[TaskSpec]
    worker: Worker
    max_workers: int = 3  # 与书图 6-4 的"最大并行度 = 3"一致
    stop_on_failure: bool = False

    def plan(self) -> list[list[TaskSpec]]:
        return build_waves(self.tasks)

    def dispatch(self) -> list[TaskResult]:
        results: list[TaskResult] = []
        for wave_index, wave in enumerate(self.plan(), start=1):
            conflicts = detect_conflicts(wave)
            if conflicts:
                # 并行安全边界：有冲突则强制串行，并记录原因
                for conflict in conflicts:
                    results.append(
                        TaskResult(
                            task_id="(scheduler)",
                            ok=False,
                            detail=f"并行边界冲突：{conflict.detail}，本波次改为串行",
                            wave=wave_index,
                            worker=self.worker.name,
                        )
                    )
                for task in wave:
                    results.append(self._run_one(task, wave_index))
                continue

            if len(wave) == 1:
                results.append(self._run_one(wave[0], wave_index))
                continue

            with ThreadPoolExecutor(max_workers=min(self.max_workers, len(wave))) as pool:
                futures = {pool.submit(self._run_one, t, wave_index): t for t in wave}
                for future in as_completed(futures):
                    results.append(future.result())

            if self.stop_on_failure and any(not r.ok for r in results):
                break
        return sorted(results, key=lambda r: (r.wave, _task_order_by_id(r.task_id)))

    def _run_one(self, task: TaskSpec, wave: int) -> TaskResult:
        started = time.perf_counter()
        try:
            ok, detail = self.worker.execute(task)
        except Exception as exc:  # noqa: BLE001 - 单个任务失败不应终止整轮委派
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        return TaskResult(
            task_id=task.task_id,
            ok=ok,
            detail=detail,
            duration=round(time.perf_counter() - started, 3),
            wave=wave,
            worker=self.worker.name,
        )


def _task_order_by_id(task_id: str) -> int:
    m = re.search(r"(\d+)", task_id)
    return int(m.group(1)) if m else 0


def summarise(results: list[TaskResult]) -> str:
    """渲染人类可读的委派报告。"""
    lines = ["层级委托执行报告", "=" * 48]
    current_wave = None
    for result in results:
        if result.wave != current_wave:
            current_wave = result.wave
            lines.append(f"--- 第 {current_wave} 波（可并行）---")
        mark = "✓" if result.ok else "✗"
        lines.append(
            f"  {mark} {result.task_id:<10} {result.duration:>6.3f}s  {result.detail}"
        )
    total = len([r for r in results if r.task_id != "(scheduler)"])
    passed = len([r for r in results if r.ok and r.task_id != "(scheduler)"])
    lines.append("=" * 48)
    lines.append(f"合计 {passed}/{total} 个任务通过")
    return "\n".join(lines)
