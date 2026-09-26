"""测试：SDD × Agent 设计模式（第 8 章）与子智能体并行执行（第 6.7 节）。

覆盖四件事：
  1. 生成—评审：能真的审出问题，也放得过合格规范
  2. 条件路由：路由表确实来自 design.md，且分组与书图 6-4 的 DAG 一致
  3. 护栏三明治：入护栏拦得住规范外改动
  4. 层级委托：按 DAG 分波，**同波任务真的并行**（用耗时与线程并发数验证）
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from sdd_agents import generate_review, guardrail, orchestrator, router
from sdd_agents.orchestrator import (
    Orchestrator,
    SubagentWorker,
    TaskSpec,
    build_waves,
    detect_conflicts,
    load_tasks,
    summarise,
)

ROOT = Path(__file__).resolve().parent.parent
SPECS = ROOT / "specs"


# ============================================================ 生成—评审

def test_review_passes_on_real_specs():
    """本项目三份规范应当通过审查（否则规范本身有质量问题）。"""
    results = generate_review.review_many(
        [SPECS / "proposal.md", SPECS / "design.md", SPECS / "tasks.md"]
    )
    failed = {r.target: [str(f) for f in r.errors] for r in results if not r.passed}
    assert not failed, f"规范审查未通过：{failed}"


def test_review_detects_missing_out_of_scope():
    bad = """# 某需求

## 1. 背景与目标
我们要做一个工具。

## 2. 功能范围
### 2.1 做什么
- 采集数据

## 3. 验收标准
- [ ] 能采集数据。性能要求：快。
"""
    result = generate_review.review_spec_from_text(bad, "proposal.md")
    rules = {f.rule for f in result.errors}
    assert "范围有界" in rules, "未检出缺少不做什么"
    assert "性能有数" in rules, "未检出性能无数字"


def test_review_detects_vague_acceptance():
    bad = """# 某需求

## 1. 背景与目标
背景。

## 2. 功能范围
### 2.2 不做什么（本期明确排除）
- 不做 A
- 不做 B
- 不做 C

## 3. 验收标准
- [ ] 生成速度尽量快
"""
    result = generate_review.review_spec_from_text(bad, "proposal.md")
    assert any("模糊表述" in f.message for f in result.errors)


def test_review_is_doc_type_aware():
    """design.md 不该被要求写"不做什么"（那是 proposal 的职责）。"""
    design_text = (SPECS / "design.md").read_text(encoding="utf-8")
    result = generate_review.review_spec_from_text(design_text, "design.md")
    assert not any(f.rule == "范围有界" for f in result.findings)


def test_llm_reviewer_stub_raises_instead_of_silently_falling_back():
    """未接入 LLM 时必须显式报错，不能静默退回确定性评审。"""
    with pytest.raises(NotImplementedError):
        generate_review.llm_reviewer_stub("text", "proposal.md")


# ============================================================== 条件路由

def test_route_table_comes_from_design_md():
    table = router.build_route_table()
    # design.md §2 的五个模块
    assert set(table.module_to_handler) == {"collector", "generator", "notifier", "shared", "main"}
    # 不应把接口契约表里的 "GitHub API" 误解析成模块
    assert "GitHub" not in table.module_to_handler


def test_tasks_3_4_5_all_route_to_collector_agent():
    """书图 6-4：三个采集模块可并行 —— 路由后应归同一执行者。"""
    tasks = load_tasks()
    decisions = router.route_all(tasks)
    by_task = {d.task_id: d for d in decisions}

    assert by_task["Task3"].handler == "collector-agent"
    assert by_task["Task4"].handler == "collector-agent"
    assert by_task["Task5"].handler == "collector-agent"


def test_module_of_file():
    assert router.module_of_file("collector/github.py") == "collector"
    assert router.module_of_file("tests/test_x.py") == "tests"
    assert router.module_of_file("main.py") == "main"
    assert router.module_of_file("") is None


def test_group_by_handler_keeps_parallel_groups():
    decisions = router.route_all(load_tasks())
    grouped = router.group_by_handler(decisions)
    # 采集层任务：Task 3（github）、Task 4（lark_task）、Task 5（lark_msg）
    # 加上 v1.1 的 Task 11（lark_attendance）——同一 handler 内部仍互相独立，可并行
    assert len(grouped["collector-agent"]) == 4
    assert len(grouped["notifier-agent"]) == 2


# ========================================================= 护栏三明治

def test_guardrails_self_check_passes():
    report = guardrail.run_guardrails()
    assert report.passed, report.summary()


@pytest.mark.parametrize(
    "path, content, expect_block",
    [
        ("specs/proposal.md", "改需求", True),
        ("collector/lark_attendance.py", "def collect(): ...", False),
        ("generator/summary.py", "def llm_summary(text): ...", True),
        ("shared/config.py", 'api_key = "abcdef1234567890"', True),
    ],
)
def test_input_guard_payloads(path, content, expect_block):
    outcome = guardrail.run_input_guard(
        {"tool_input": {"file_path": path, "content": content}}
    )
    assert outcome.blocked is expect_block, outcome.stderr


# ================================================ 层级委托 + 并行执行

def test_load_tasks_parses_all_eleven_tasks():
    tasks = load_tasks()
    ids = [t.task_id for t in tasks]
    assert ids == [f"Task{i}" for i in range(1, len(ids) + 1)]
    assert len(tasks) >= 10


def test_parsed_dependencies_match_the_book_dag():
    by_id = {t.task_id: t for t in load_tasks()}
    assert by_id["Task1"].dependencies == []
    assert by_id["Task9"].dependencies == ["Task3", "Task4", "Task5", "Task6", "Task7", "Task8"]
    assert by_id["Task10"].dependencies == ["Task9"]


def test_parsed_outputs_are_real_files():
    """tasks.md 里声明的产出文件必须真实存在（规范与代码一致）。"""
    tasks = load_tasks()
    declared: list[str] = []
    for task in tasks:
        declared.extend(task.outputs)
    missing = [
        o for o in declared
        if o.endswith(".py") and not (ROOT / o).exists()
    ]
    assert not missing, f"tasks.md 声明但实际不存在的产出文件：{missing}"


def test_build_waves_groups_independent_tasks():
    tasks = load_tasks()
    waves = build_waves(tasks)
    by_wave = {i: {t.task_id for t in w} for i, w in enumerate(waves, 1)}

    # 第 1 波只有 Task1（无依赖）
    assert by_wave[1] == {"Task1"}
    # Task3/4/5/6 依赖 Task2，应在同一波
    for wave_ids in by_wave.values():
        if "Task3" in wave_ids:
            assert {"Task3", "Task4", "Task5", "Task6"} <= wave_ids
            break
    else:
        pytest.fail("找不到包含 Task3 的波次")


def test_build_waves_rejects_cycle():
    cyclic = [
        TaskSpec(task_id="Task1", title="a", dependencies=["Task2"]),
        TaskSpec(task_id="Task2", title="b", dependencies=["Task1"]),
    ]
    with pytest.raises(ValueError, match="存在环"):
        build_waves(cyclic)


def test_detect_conflicts_flags_shared_output():
    wave = [
        TaskSpec(task_id="Task1", title="a", outputs=["shared/config.py"]),
        TaskSpec(task_id="Task2", title="b", outputs=["shared/config.py"]),
    ]
    conflicts = detect_conflicts(wave)
    assert conflicts and conflicts[0].kind == "shared_output"


def test_orchestrator_with_fake_worker_runs_in_wave_order():
    tasks = load_tasks()
    worker = SubagentWorker(name="fake")
    results = Orchestrator(tasks=tasks, worker=worker).dispatch()

    assert all(r.ok for r in results)
    # 每个任务都被委派一次（Task 11 依赖 Task 2，因此不在 Task 10 之后，而在第 2 波）
    assert len(worker.calls) == len(tasks) == 11
    assert set(worker.calls) == {f"Task{i}" for i in range(1, 12)}
    # 依赖顺序：Task1 必须在 Task2 之前（依赖顺序）
    assert worker.calls.index("Task1") < worker.calls.index("Task2")
    # 委派顺序必须尊重每一波的依赖：任一任务都不得早于它的依赖出现
    for wave in build_waves(tasks):
        positions = [worker.calls.index(t.task_id) for t in wave]
        for task in wave:
            for dep in task.dependencies:
                assert worker.calls.index(dep) < worker.calls.index(task.task_id), (
                    f"{task.task_id} 早于其依赖 {dep} 执行"
                )
        # 同一波内部顺序任意，但不能与其它波交错
        assert max(positions) - min(positions) + 1 == len(positions)


def test_same_wave_tasks_run_concurrently():
    """第 6.7 节核心：同一波次的任务必须**并发**执行，而不是排队。"""
    tasks = load_tasks()
    concurrency = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def behaviour(task: TaskSpec) -> tuple[bool, str]:
        with lock:
            concurrency["now"] += 1
            concurrency["peak"] = max(concurrency["peak"], concurrency["now"])
        time.sleep(0.05)
        with lock:
            concurrency["now"] -= 1
        return True, "ok"

    worker = SubagentWorker(name="parallel", behaviour=behaviour)
    started = time.perf_counter()
    results = Orchestrator(tasks=tasks, worker=worker, max_workers=3).dispatch()
    elapsed = time.perf_counter() - started

    assert all(r.ok for r in results)
    # 书图 6-4：最大并行度 3 —— 峰值并发应达到 2 以上
    assert concurrency["peak"] >= 2, f"未观察到并发，峰值={concurrency['peak']}"
    # 串行执行的下限是"任务数 × 单任务耗时"；并发后必须明显低于这个下限
    serial_lower_bound = len(tasks) * 0.05
    assert elapsed < serial_lower_bound * 0.8, (
        f"耗时 {elapsed:.3f}s 接近串行下限 {serial_lower_bound:.3f}s，说明没有真正并行"
    )


def test_max_workers_caps_parallelism():
    tasks = load_tasks()
    concurrency = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def behaviour(task: TaskSpec) -> tuple[bool, str]:
        with lock:
            concurrency["now"] += 1
            concurrency["peak"] = max(concurrency["peak"], concurrency["now"])
        time.sleep(0.03)
        with lock:
            concurrency["now"] -= 1
        return True, "ok"

    worker = SubagentWorker(name="capped", behaviour=behaviour)
    Orchestrator(tasks=tasks, worker=worker, max_workers=1).dispatch()

    assert concurrency["peak"] == 1, f"max_workers=1 时不应并发，峰值={concurrency['peak']}"


def test_worker_failure_does_not_abort_whole_delegation():
    """design.md §6.1：单点失败不应阻断整体（优雅降级）。"""
    tasks = load_tasks()

    def behaviour(task: TaskSpec) -> tuple[bool, str]:
        return (False, "模拟失败") if task.task_id == "Task3" else (True, "ok")

    worker = SubagentWorker(name="flaky", behaviour=behaviour)
    results = Orchestrator(tasks=tasks, worker=worker).dispatch()

    assert len([r for r in results if r.task_id != "(scheduler)"]) == len(tasks)
    assert any(not r.ok for r in results)
    assert any(r.ok for r in results)


def test_summarise_renders_report():
    tasks = load_tasks()
    results = Orchestrator(tasks=tasks, worker=SubagentWorker()).dispatch()
    text = summarise(results)
    assert "层级委托执行报告" in text
    assert "波" in text
    assert "个任务通过" in text


def test_pytest_verifier_runs_real_tests():
    """默认工作者应真的调用 pytest，并以退出码作为判据。"""
    verifier = orchestrator.PytestVerifier(test_paths=("tests/test_contracts.py",))
    task = TaskSpec(task_id="TaskX", title="契约一致性")
    ok, detail = verifier.execute(task)
    assert ok, detail
    assert "passed" in detail
