"""回归测试：Dockerfile 必须保持"镜像内可运行"。

本机没有容器运行时，无法真的 docker build；这里用等价的静态+动态校验，
至少保证 Dockerfile 不会退化成"写了但镜像里跑不起来"。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VERIFIER = ROOT / "scripts" / "verify_dockerfile.py"


@pytest.fixture(scope="module")
def dockerfile_text() -> str:
    return (ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_dockerfile_exists(dockerfile_text):
    assert dockerfile_text.strip()


def test_dockerfile_copies_specs_directory(dockerfile_text):
    """specs/ 必须在镜像内：入护栏要据此判断是否越界实现。"""
    assert "COPY specs/" in dockerfile_text


def test_dockerfile_copies_guard_scripts(dockerfile_text):
    """scripts/ 必须在镜像内，否则第 8 章的护栏在容器环境下形同虚设。"""
    assert "COPY scripts/" in dockerfile_text
    assert "COPY CLAUDE.md" in dockerfile_text


def test_dockerfile_declares_data_volume(dockerfile_text):
    """SQLite 日报历史（ADR-002）需要持久化。"""
    assert "VOLUME" in dockerfile_text and "/app/data" in dockerfile_text


def test_dockerfile_does_not_bake_real_config(dockerfile_text):
    """真实 config.yaml 含环境相关参数，不应烧进镜像。"""
    assert "COPY config.yaml.example" in dockerfile_text
    assert "COPY config.yaml " not in dockerfile_text


def test_dockerfile_uses_python_311_or_newer(dockerfile_text):
    """proposal.md §2.3：开发语言 Python 3.11 及更高版本。"""
    from_lines = [
        line for line in dockerfile_text.splitlines()
        if line.strip().upper().startswith("FROM ")
    ]
    assert from_lines, "Dockerfile 里找不到 FROM 指令"
    first = from_lines[0].split()[1]  # 例如 python:3.11-slim
    assert first.startswith("python:"), f"基础镜像应为 python，实际为 {first}"
    version = first.split(":", 1)[1].split("-")[0]
    major, minor = (int(x) for x in version.split(".")[:2])
    assert (major, minor) >= (3, 11), f"Python 版本过低：{version}"


def test_verify_dockerfile_script_passes():
    """完整跑一遍校验脚本（静态 + 按 COPY 清单搭建后运行 --mock）。"""
    result = subprocess.run(
        [sys.executable, str(VERIFIER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 0, f"Dockerfile 校验未通过：\n{output}"
    assert "校验通过" in output
