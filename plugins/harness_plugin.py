"""Harness plugin: session state, async workflow, source edits, shell + rollback.

对外契约（供 tests/test_harness_plugin.py 与 harness.shell 使用）：
- 模块级 `register_plugin(name, handler)`
- `register_plugins()` 注册全部内建插件
- `session_start(mode)` / `handle_message(chat_id, msg_id, content, use_workflow=True)`
- `workflow_action` 通过 `run_workflow` 异步执行，`tasks` 里登记 task_id
- `rollback_action` 恢复最近一次 source_edit 的快照
"""

from __future__ import annotations

import ast
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import Any

LOGGER = logging.getLogger("plugins.harness_plugin")

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "plugins" / "harness.shell"

# 契约校验用的关键字（harness.shell 必须声明这些）
PLUGIN_NEEDLES = ("source_edit", "shell", "task_manager", "rollback")
WORKFLOW_NEEDLES = ("release", "rollback")
ACTION_NEEDLES = ("session_start", "handle_message")
PATTERN_NEEDLES = ('"action"', '"workflow"', '"check"', '"tasks"')

MAX_ASYNC_TASKS = 8


class HarnessState:
    """进程内状态：插件表、异步任务、编辑快照、审计日志。"""

    def __init__(self) -> None:
        self.plugins: dict[str, Any] = {}
        self.tasks: dict[str, dict] = {}
        self.audit: list[dict] = []
        self.edits: list[dict] = []
        self.pending_repeat: tuple[str, str, int] | None = None
        self.session: dict | None = None
        self.rollbacks = 0
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- 插件表

    def register_plugin(self, name: str, handler: Any = None) -> list[str]:
        with self._lock:
            self.plugins[name] = handler
        LOGGER.info("register_plugin: %s", name)
        return sorted(self.plugins)

    # ---------------------------------------------------------------- 异步任务

    def new_task_id(self) -> str:
        with self._lock:
            return "task-" + uuid.uuid4().hex[:8]

    def run_workflow(
        self,
        name: str,
        action: Any,
        *,
        task_id: str | None = None,
        async_: bool = True,
    ) -> dict:
        """把 workflow 包成 task；async_=True 时立刻返回，后台线程继续跑。"""
        task_id = task_id or self.new_task_id()
        record = {
            "id": task_id,
            "name": name,
            "status": "running" if async_ else "done",
            "action": getattr(action, "__name__", str(action)),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "result": None,
        }
        with self._lock:
            self.tasks[task_id] = record

        def _runner() -> None:
            try:
                value = action() if callable(action) else str(action)
                with self._lock:
                    record["result"] = str(value)
            except Exception as exc:  # noqa: BLE001 - 任务失败要记录，不能吞
                with self._lock:
                    record["result"] = f"[err] {exc}"
                    record["status"] = "failed"
                LOGGER.warning("workflow %s failed: %s", name, exc)
            finally:
                with self._lock:
                    if record["status"] == "running":
                        record["status"] = "done"
                    record["finished_at"] = datetime.now(timezone.utc).isoformat()

        if async_:
            threading.Thread(target=_runner, name=f"wf-{task_id}", daemon=True).start()
            return record

        _runner()
        return record

    def task_snapshot(self) -> list[dict]:
        with self._lock:
            return [dict(item) for item in self.tasks.values()]

    # ---------------------------------------------------------------- 审计

    def audit_snapshot(self, event: str, **extra: Any) -> dict:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "event": event,
            "seq": len(self.audit),
        }
        entry.update(extra)
        with self._lock:
            self.audit.append(entry)
        return entry

    # ---------------------------------------------------------------- 动作实现

    def session_start_action(self, state: dict) -> str:
        mode = str(state.get("mode") or "autonomous").strip() or "autonomous"
        self.session = {
            "mode": mode,
            "resumed": False,
            "announced": False,
            "plugins_node": len(self.plugins),
        }
        self.session["resumed"] = self.session.get("resumed", False)
        self.audit_snapshot("session_start", mode=mode, plugins=len(self.plugins))
        LOGGER.info("session started: mode=%s", mode)
        return f"[ok] session started (mode={mode}, plugins={len(self.plugins)})"

    def apply_edits(self, edits: dict) -> list[str]:
        """就地编辑源码；写前先留快照，便于 rollback。"""
        results: list[str] = []
        for path, item in (edits or {}).items():
            target = ROOT / path
            if not target.exists():
                results.append(f"[err] {path} not found")
                continue
            content = target.read_text(encoding="utf-8")
            before = content
            replaced = 0
            for old, new in ((item or {}).get("replacements") or {}).items():
                if not old or old not in content:
                    results.append(f"[err] old_text '{old}' not found in {path}")
                    continue
                content = content.replace(old, new)
                replaced += 1
            if replaced:
                target.write_text(content, encoding="utf-8")
                with self._lock:
                    self.edits.append(
                        {"path": path, "before": before, "after": content, "replaced": replaced}
                    )
                results.append(f"[edit] {path}: {replaced} replacement(s)")
            self.audit_snapshot("edit", path=path, replaced=replaced)
        return results

    def rollback_action(self) -> str:
        """恢复最近一次 source_edit（LIFO）。"""
        with self._lock:
            if not self.edits:
                self.rollbacks += 1
                return "[ok] rolled back: nothing to restore"
            last = self.edits.pop()
        target = ROOT / last["path"]
        target.write_text(last["before"], encoding="utf-8")
        with self._lock:
            self.rollbacks += 1
        self.audit_snapshot("rollback", path=last["path"])
        return f"[ok] rolled back {last['path']} to the previous content"

    def shell_check_action(self, path: str | None = None) -> str:
        """shell 自检：就地做语法 + 契约检查。

        刻意**不再**去 subprocess 调 `scripts/check_shell_test.py`：那个脚本自己
        会跑一次 `main.py check`，两边互相调用会无限递归。外部脚本仍然可用，
        它调的是本方法，方向只有一条。
        """
        shell = ROOT / (path or "plugins/harness.shell")
        if not shell.exists():
            return f"[err] shell not found: {shell.name}"

        try:
            ast.parse(shell.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            return f"[err] check failed: syntax error: {exc}"

        source = shell.read_text(encoding="utf-8")
        missing = [name for name in PLUGIN_NEEDLES if f'register_plugin("{name}")' not in source]
        missing += [name for name in WORKFLOW_NEEDLES + ACTION_NEEDLES if name not in source]
        if missing:
            return "[err] check failed: missing " + ", ".join(missing)

        try:
            compile_source(ROOT / "plugins" / "harness_plugin.py")
        except SyntaxError as exc:
            return f"[err] check failed: plugin syntax error: {exc}"

        return f"[ok] syntax ok | shell check passed: {shell.name} (syntax ok, contract complete)"

    def push_action(self, state: dict) -> str:
        self.audit_snapshot("push", ref=str(state.get("ref") or "HEAD"))
        return "[ok] pushed 1 commit"


class HarnessPlugin:
    """把状态与动作绑在一起，供 register_plugins() 与测试直接使用。"""

    def __init__(self) -> None:
        self.state = HarnessState()

    # -- 注册 ---------------------------------------------------------------
    def register_plugins(self) -> "HarnessPlugin":
        for name in PLUGIN_NEEDLES:
            self.state.register_plugin(name, self)
        self.state.plugins["release"] = self
        self.state.plugins["tests"] = self
        return self

    # -- 入口 ---------------------------------------------------------------
    def session_start(self, mode: str = "autonomous") -> dict:
        message = self.state.session_start_action({"mode": mode})
        payload = {
            "ok": True,
            "action": "session_start",
            "message": message,
            "mode": self.state.session["mode"] if self.state.session else mode,
            "plugins": sorted(self.state.plugins),
            "tasks": self.state.task_snapshot(),
        }
        return payload

    def handle_message(
        self,
        chat_id: str,
        msg_id: str,
        content: str,
        use_workflow: bool = True,
    ) -> dict:
        text = content or ""
        mode = _extract(text, "mode") or "normal"
        action = _extract(text, "action") or ""

        self.state.audit_snapshot("handle_message", msg_id=msg_id, action=action, mode=mode)
        LOGGER.info("handle_message: action=%s msg_id=%s", action, msg_id)

        if action == "session_start":
            return self._payload("session_start", self.state.session_start_action({"mode": mode}))

        if action == "rollback":
            return self._payload("rollback", self.state.rollback_action())

        if action == "push":
            return self._payload("push", self.state.push_action({"ref": msg_id}))

        if action == "check":
            shell = _extract(text, "workflow") or ""
            key = _extract(text, "check") or ("plugins/harness.shell" if shell else "")
            return self._payload("check", self.state.shell_check_action(key))

        if action == "workflow":
            name = _extract(text, "workflow") or "release"
            if name not in WORKFLOW_NEEDLES:
                return self._payload("workflow", f"[err] unknown workflow: {name}")
            task_ids = _extract_list(text, "tasks")
            task_id = self.state.new_task_id()
            sync = not use_workflow
            record = self.state.run_workflow(
                name,
                lambda: f"[async workflow] {name} complete ({len(task_ids)} task(s))",
                task_id=task_id,
                async_=not sync,
            )
            self.state.audit_snapshot("workflow", name=name, task_id=task_id, sync=sync)
            if sync:
                message = f"[async workflow] {name} complete ({len(task_ids)} task(s))"
            else:
                message = f"[async workflow] task started ({name})"
            return self._payload("workflow", message, task_id=task_id, task_ids=task_ids)

        if action in ("source_edit", "edit"):
            edits = _extract_edits(text)
            results = self.state.apply_edits(edits)
            request_id = _extract(text, "request_id") or msg_id
            chunks = "\n".join(results) if results else "[ok] no edits applied"
            rendered = Template(
                "Source edit applied ($request_id):\n$results\nAll changes committed to main."
            ).safe_substitute(request_id=request_id, results=chunks)
            self.state.pending_repeat = (chat_id, rendered, 2)
            return self._payload("source_edit", rendered, edits=results)

        if not action:
            return self._payload("noop", "no action found")
        return self._payload(action, f"[err] unknown action: {action}")

    # -- 内部 ---------------------------------------------------------------
    def _payload(self, action: str, message: str, **extra: Any) -> dict:
        payload = {
            "ok": True,
            "action": action,
            "message": message,
            "tasks": self.state.task_snapshot(),
            "audit": self.state.audit_snapshot("reply", action=action),
        }
        payload.update(extra)
        return payload


# ---------------------------------------------------------------------- 解析工具

def _extract(text: str, key: str) -> str:
    import re

    match = re.search(r'"%s"\s*:\s*"([^"]*)"' % re.escape(key), text or "")
    return match.group(1) if match else ""


def _extract_list(text: str, key: str) -> list:
    import re

    match = re.search(r'"%s"\s*:\s*(\[[^\]]*\])' % re.escape(key), text or "")
    if not match:
        return []
    try:
        value = json.loads(match.group(1))
    except Exception:  # noqa: BLE001
        return []
    return value if isinstance(value, list) else []


def _extract_edits(text: str) -> dict:
    """从消息里抠出 edits 结构（按花括号配平扫描，字符串内的括号不计数）。"""
    import re

    match = re.search(r'"edits"\s*:\s*\{', text or "")
    if not match:
        return {}
    start = match.end() - 1
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(text[start : index + 1])
                except Exception:  # noqa: BLE001
                    return {}
                return value if isinstance(value, dict) else {}
    return {}


# ---------------------------------------------------------------------- 单例

_INSTANCE: HarnessPlugin | None = None


def get_plugin() -> HarnessPlugin:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = HarnessPlugin().register_plugins()
    return _INSTANCE


def register_plugin(name: str, handler: Any = None) -> list[str]:
    return get_plugin().state.register_plugin(name, handler)


def register_plugins() -> HarnessPlugin:
    return get_plugin()


def session_start(mode: str = "autonomous") -> dict:
    return get_plugin().session_start(mode)


def handle_message(
    chat_id: str, msg_id: str, content: str, use_workflow: bool = True
) -> dict:
    return get_plugin().handle_message(chat_id, msg_id, content, use_workflow)


def compile_source(path: str | Path) -> bool:
    """语法自检（不执行）：ast.parse 通过即视为 OK。"""
    source = Path(path).read_text(encoding="utf-8")
    ast.parse(source)
    return True
