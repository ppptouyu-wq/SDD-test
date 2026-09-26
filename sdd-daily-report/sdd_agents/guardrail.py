"""护栏三明治模式（第 8 章 8.4）：规范作为安全护栏。

书中要点：
  - 8.4.1 模式简介：入护栏（写文件前拦截越界）→ 执行 → 出护栏（执行后自动验证）。
  - 8.4.2 SDD 规范作为护栏的素材：护栏的判断依据来自 proposal.md §2.2 的排除项、
    design.md §6.2 的安全约束、以及"不得修改 specs/"的规范治理规则。
  - 8.4.3 在 Claude Code 中实现护栏：用 Hooks 的 PreToolUse / PostToolUse 挂载。

真正的护栏实现在 `scripts/input_guard.py`（PreToolUse）与
`scripts/output_guard.py`（PostToolUse），由 `.claude/settings.json` 注册。
本模块是它们的**可测试封装**：把子进程调用变成可断言的对象，
并额外提供"自检"能力 —— 用一组已知违规样本确认护栏确实拦得住。
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INPUT_GUARD = ROOT / "scripts" / "input_guard.py"
OUTPUT_GUARD = ROOT / "scripts" / "output_guard.py"

GUARD_BLOCK = 2
GUARD_ALLOW = 0


@dataclass
class GuardOutcome:
    """一次护栏检查的结果。"""

    passed: bool
    exit_code: int
    stderr: str = ""

    @property
    def blocked(self) -> bool:
        return self.exit_code == GUARD_BLOCK


@dataclass
class SelfCheckCase:
    """护栏自检样本。"""

    name: str
    payload: dict
    should_block: bool


@dataclass
class GuardrailReport:
    """护栏自检报告。"""

    cases: list[tuple[SelfCheckCase, GuardOutcome]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(
            outcome.blocked == case.should_block for case, outcome in self.cases
        )

    def summary(self) -> str:
        lines = ["护栏三明治自检", "=" * 48]
        for case, outcome in self.cases:
            expect = "应拦截" if case.should_block else "应放行"
            actual = "已拦截" if outcome.blocked else "已放行"
            mark = "✓" if outcome.blocked == case.should_block else "✗"
            lines.append(f"  {mark} {case.name:<24} {expect} → {actual}")
        lines.append("=" * 48)
        lines.append("自检通过：入护栏能拦住规范外改动" if self.passed else "自检失败")
        return "\n".join(lines)


def run_input_guard(payload: dict) -> GuardOutcome:
    """调用入护栏（PreToolUse）。payload 形如 Claude Code 传入的钩子结构。"""
    return _invoke(INPUT_GUARD, json.dumps(payload, ensure_ascii=False))


def run_output_guard() -> GuardOutcome:
    """调用出护栏（PostToolUse）—— 会真的跑一遍全量测试。"""
    return _invoke(OUTPUT_GUARD, "")


def _invoke(script: Path, stdin_text: str) -> GuardOutcome:
    if not script.exists():
        raise FileNotFoundError(f"护栏脚本不存在：{script}")
    proc = subprocess.run(
        [sys.executable, str(script)],
        input=stdin_text,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return GuardOutcome(
        passed=proc.returncode == GUARD_ALLOW,
        exit_code=proc.returncode,
        stderr=(proc.stderr or "").strip(),
    )


def default_self_check_cases() -> list[SelfCheckCase]:
    """护栏必须拦住的四类情况 + 一个正常情况。"""
    return [
        SelfCheckCase(
            name="直接修改 specs/ 规范",
            payload={
                "tool_input": {"file_path": "specs/proposal.md", "content": "改需求"}
            },
            should_block=True,
        ),
        SelfCheckCase(
            name="越界实现智能摘要",
            payload={
                "tool_input": {
                    "file_path": "generator/summary.py",
                    "content": "def llm_summary(text): ...",
                }
            },
            should_block=True,
        ),
        SelfCheckCase(
            name="越界实现多租户",
            payload={
                "tool_input": {
                    "file_path": "shared/tenant.py",
                    "content": "def enable_multi_tenant(): ...",
                }
            },
            should_block=True,
        ),
        SelfCheckCase(
            name="硬编码密钥",
            payload={
                "tool_input": {
                    "file_path": "shared/config.py",
                    "content": 'api_key = "abcdef1234567890"',
                }
            },
            should_block=True,
        ),
        SelfCheckCase(
            name="正常实现采集模块",
            payload={
                "tool_input": {
                    "file_path": "collector/github.py",
                    "content": "def collect(repos, since, until): ...",
                }
            },
            should_block=False,
        ),
    ]


def run_guardrails(*, include_output_guard: bool = False) -> GuardrailReport:
    """跑一遍护栏自检。

    :param include_output_guard: 是否也跑出护栏（会执行全量测试，较慢）。
    """
    report = GuardrailReport()
    for case in default_self_check_cases():
        report.cases.append((case, run_input_guard(case.payload)))

    if include_output_guard:
        case = SelfCheckCase(name="出护栏（跑全量测试）", payload={}, should_block=False)
        report.cases.append((case, run_output_guard()))
    return report
