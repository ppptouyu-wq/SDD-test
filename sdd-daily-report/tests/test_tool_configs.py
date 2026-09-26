"""测试：多工具规范配置的同步性（第 3 章 3.2 节）。

核心断言：四类工具的规则文件必须与唯一事实来源 `CLAUDE.md` 保持同步。
这防止"改了 CLAUDE.md 忘了改 .cursorrules"导致的规范漂移 ——
用不同工具的团队成员会遵守不同规则，这在本书语境下是严重问题。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.generate_tool_configs import (  # noqa: E402
    COPILOT_INSTRUCTIONS,
    CURSOR_LEGACY,
    CURSOR_RULES,
    GENERATED_MARKER,
    KIRO_STEERING,
    check_sync,
    extract_sections,
    generate,
    read_source,
)

CONFIG_FILES = [CURSOR_RULES, CURSOR_LEGACY, COPILOT_INSTRUCTIONS, KIRO_STEERING]


@pytest.mark.parametrize("path", CONFIG_FILES, ids=lambda p: p.name)
def test_tool_config_exists(path):
    assert path.exists(), f"缺少工具配置文件：{path}"


@pytest.mark.parametrize("path", CONFIG_FILES, ids=lambda p: p.name)
def test_tool_config_is_marked_generated(path):
    """必须标明是生成的，否则有人会手工改它，下次生成就被覆盖。"""
    assert GENERATED_MARKER in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", CONFIG_FILES, ids=lambda p: p.name)
def test_tool_config_contains_core_rules(path):
    """每份配置都要带上核心约束，否则该工具下的 AI 会缺少红线。"""
    text = path.read_text(encoding="utf-8")
    assert "specs/" in text, "缺少规范文件索引"
    assert "不写规范外的功能" in text or "规范外的功能" in text
    assert "禁止硬编码密钥" in text


def test_all_configs_are_in_sync_with_claude_md():
    """核心断言：四份配置与 CLAUDE.md 完全同步。"""
    stale = check_sync()
    assert not stale, (
        "以下文件与 CLAUDE.md 不同步，请运行 "
        f"`python scripts/generate_tool_configs.py`：{[str(p) for p in stale]}"
    )


def test_extract_sections_pulls_expected_headings():
    body = extract_sections(read_source())
    for name in ("项目规范", "开发规则", "目录结构"):
        assert f"## {name}" in body


def test_generate_produces_four_configs():
    generated = generate()
    assert len(generated) == 4
    assert set(generated) == set(CONFIG_FILES)


def test_cursor_rules_has_frontmatter():
    """Cursor 的 .mdc 需要 frontmatter 才能被识别生效范围。"""
    text = CURSOR_RULES.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "alwaysApply: true" in text
    assert "globs:" in text


def test_kiro_steering_maps_specs_workflow():
    """书 3.2.4：Kiro 把 Spec 流程内置进 IDE —— 要说明与三份规范的对应。"""
    text = KIRO_STEERING.read_text(encoding="utf-8")
    assert "inclusion: always" in text
    assert "requirements.md" in text and "proposal.md" in text


def test_copilot_instructions_located_where_copilot_reads_it():
    assert COPILOT_INSTRUCTIONS.parent.name == ".github"
    assert COPILOT_INSTRUCTIONS.name == "copilot-instructions.md"


def test_check_mode_passes_on_synced_repo():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_tool_configs.py"), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "同步" in result.stdout


def test_spec_check_workflow_runs_config_sync_check():
    """规范 CI 应包含"工具配置是否同步"这一步。"""
    workflow = (ROOT / ".github" / "workflows" / "spec-check.yml").read_text(encoding="utf-8")
    assert "generate_tool_configs.py" in workflow
