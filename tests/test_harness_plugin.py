"""harness 外壳与插件的单元测试（v1.8 附加交付）。

覆盖契约：
- `plugins/harness_plugin.py` 的插件注册、会话开始、异步 workflow、源码编辑、
  shell 自检、回滚与审计；
- `plugins/harness.shell` 的语法与契约完整性（由 `scripts/check_shell_test.py` 校验）；
- 根 `main.py` 的 CLI 入口。

测试不依赖网络，也不修改仓库里的既有文件：源码编辑测试只动一个临时草稿文件。
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins import harness_plugin as hp  # noqa: E402

SHELL = ROOT / "plugins" / "harness.shell"
SCRATCH = ROOT / "_harness_scratch.txt"


@pytest.fixture
def plugin() -> hp.HarnessPlugin:
    return hp.HarnessPlugin().register_plugins()


@pytest.fixture
def scratch() -> Path:
    SCRATCH.write_text("alpha\nbeta\n", encoding="utf-8")
    try:
        yield SCRATCH
    finally:
        SCRATCH.unlink(missing_ok=True)


# ---------------------------------------------------------------- 插件注册

def test_register_plugins_exposes_the_documented_plugins(plugin: hp.HarnessPlugin):
    for name in ("source_edit", "shell", "task_manager", "rollback"):
        assert name in plugin.state.plugins


def test_module_level_register_plugin_returns_the_sorted_table():
    names = hp.register_plugin("unit_test_plugin", None)

    assert "unit_test_plugin" in names
    assert names == sorted(names)


# ---------------------------------------------------------------- 会话

def test_session_start_reports_mode_and_plugin_table(plugin: hp.HarnessPlugin):
    payload = plugin.session_start("autonomous")

    assert payload["ok"] is True
    assert payload["mode"] == "autonomous"
    assert "source_edit" in payload["plugins"]
    assert payload["message"].startswith("[ok] session started")


def test_session_start_defaults_to_autonomous(plugin: hp.HarnessPlugin):
    assert plugin.session_start("")["mode"] == "autonomous"


# ---------------------------------------------------------------- 异步 workflow

def test_handle_message_starts_the_workflow_asynchronously(plugin: hp.HarnessPlugin):
    message = json.dumps({"action": "workflow", "workflow": "release", "tasks": ["a", "b"]})

    payload = plugin.handle_message("cli", "m1", message)

    assert payload["ok"] is True
    assert "async workflow" in payload["message"]
    assert payload["task_ids"] == ["a", "b"]
    assert payload["tasks"], "异步任务必须登记到 tasks 表"
    task_id = payload["task_id"]
    assert task_id.startswith("task-")
    assert plugin.state.tasks[task_id]["name"] == "release"
    assert [item["id"] for item in payload["tasks"]] == [task_id]


def test_handle_message_can_run_the_workflow_synchronously(plugin: hp.HarnessPlugin):
    message = json.dumps({"action": "workflow", "workflow": "release", "tasks": []})

    payload = plugin.handle_message("cli", "m2", message, use_workflow=False)

    assert "async workflow" in payload["message"]
    assert "complete" in payload["message"]


def test_unknown_workflow_is_rejected(plugin: hp.HarnessPlugin):
    message = json.dumps({"action": "workflow", "workflow": "nope"})

    payload = plugin.handle_message("cli", "m3", message)

    assert "unknown workflow" in payload["message"]


# ---------------------------------------------------------------- shell 自检

def test_handle_message_check_reports_the_shell_contract(plugin: hp.HarnessPlugin):
    message = json.dumps({"action": "check", "check": "plugins/harness.shell"})

    payload = plugin.handle_message("cli", "m4", message)

    assert "syntax ok" in payload["message"] or "check failed" in payload["message"]
    assert payload["ok"] is True


def test_check_shell_script_passes_on_the_shipped_shell():
    completed = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "scripts" / "check_shell_test.py"), str(SHELL)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        cwd=str(ROOT),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "shell check passed" in completed.stdout


def test_shell_declares_the_shell_contract():
    source = SHELL.read_text(encoding="utf-8")

    ast.parse(source)  # 语法必须通过
    for needle in hp.PLUGIN_NEEDLES:
        assert f'register_plugin("{needle}")' in source
    for needle in hp.WORKFLOW_NEEDLES + hp.ACTION_NEEDLES:
        assert needle in source
    for needle in hp.PATTERN_NEEDLES:
        assert needle in source
    assert "safe_substitute" in source
    assert "$results" in source


# ---------------------------------------------------------------- 源码编辑与回滚

def test_source_edit_replaces_text_and_queues_a_repeat_reply(plugin: hp.HarnessPlugin, scratch: Path):
    message = json.dumps(
        {
            "action": "source_edit",
            "request_id": "req-7",
            "edits": {SCRATCH.name: {"replacements": {"alpha": "ALPHA"}}},
        }
    )

    payload = plugin.handle_message("cli", "m5", message)

    assert "1 replacement" in payload["message"]
    assert scratch.read_text(encoding="utf-8").startswith("ALPHA")
    assert plugin.state.pending_repeat is not None
    chat_id, reply, repeat = plugin.state.pending_repeat
    assert (chat_id, repeat) == ("cli", 2)
    assert "req-7" in reply


def test_source_edit_reports_a_missing_old_text(plugin: hp.HarnessPlugin, scratch: Path):
    message = json.dumps(
        {
            "action": "source_edit",
            "edits": {SCRATCH.name: {"replacements": {"{old}": "new"}}},
        }
    )

    payload = plugin.handle_message("cli", "m6", message)

    assert "{old}" in payload["message"]
    assert "not found" in payload["message"]
    assert scratch.read_text(encoding="utf-8") == "alpha\nbeta\n"  # 未改动


def test_rollback_restores_the_last_edit(plugin: hp.HarnessPlugin, scratch: Path):
    edit = json.dumps(
        {
            "action": "source_edit",
            "edits": {SCRATCH.name: {"replacements": {"beta": "BETA"}}},
        }
    )
    plugin.handle_message("cli", "m7", edit)

    payload = plugin.handle_message("cli", "m8", json.dumps({"action": "rollback"}))

    assert "rolled back" in payload["message"]
    assert scratch.read_text(encoding="utf-8") == "alpha\nbeta\n"
    assert plugin.state.rollbacks == 1


def test_rollback_without_edits_is_a_no_op(plugin: hp.HarnessPlugin):
    payload = plugin.handle_message("cli", "m9", json.dumps({"action": "rollback"}))

    assert "rolled back" in payload["message"]


# ---------------------------------------------------------------- 其它

def test_handle_message_without_action_is_a_noop(plugin: hp.HarnessPlugin):
    payload = plugin.handle_message("cli", "m10", "just chatting")

    assert payload["action"] == "noop"
    assert payload["ok"] is True


def test_audit_log_records_every_action(plugin: hp.HarnessPlugin):
    plugin.session_start("autonomous")
    plugin.handle_message("cli", "m11", json.dumps({"action": "push"}))

    events = [entry["event"] for entry in plugin.state.audit]

    assert "session_start" in events
    assert "reply" in events
    assert all(isinstance(entry["seq"], int) for entry in plugin.state.audit)


def test_compile_source_accepts_the_plugin_and_the_shell():
    assert hp.compile_source(ROOT / "plugins" / "harness_plugin.py") is True
    assert hp.compile_source(SHELL) is True


# ---------------------------------------------------------------- CLI 入口

def test_cli_session_start_prints_json():
    completed = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "main.py"), "session_start", "--mode", "autonomous"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        cwd=str(ROOT),
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["mode"] == "autonomous"
    assert "source_edit" in payload["plugins"]
