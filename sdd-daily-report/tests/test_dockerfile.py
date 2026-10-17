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


# ---------------------------------------------------------------------------
# v1.12：镜像质量的四条硬约束（specs/design.md §7.2）
# ---------------------------------------------------------------------------


def test_dockerfile_is_multi_stage(dockerfile_text):
    """多阶段构建：依赖装在 builder 里，runtime 只搬运产物。"""
    from_lines = [
        line.strip() for line in dockerfile_text.splitlines()
        if line.strip().upper().startswith("FROM ")
    ]
    assert len(from_lines) >= 2, "v1.12 要求 builder / runtime 两段"
    assert "--from=" in dockerfile_text, "没有从 builder 阶段 COPY 产物，分阶段就没意义"


def test_dockerfile_runs_as_non_root(dockerfile_text):
    """容器内进程不得以 uid 0 运行。"""
    user_lines = [
        line.strip() for line in dockerfile_text.splitlines()
        if line.strip().upper().startswith("USER ")
    ]
    assert user_lines, "缺少 USER 指令：镜像会以 root 运行"
    names = {line.split(maxsplit=1)[1].strip() for line in user_lines}
    assert not (names & {"root", "0", "0:0"}), f"USER 是 root：{names}"


def test_dockerfile_declares_healthcheck(dockerfile_text):
    """容器状态要能被 docker ps 观测；且健康检查不依赖外部配置。"""
    assert "HEALTHCHECK" in dockerfile_text
    healthcheck = dockerfile_text.split("HEALTHCHECK", 1)[1]
    assert "import main" in healthcheck, "健康检查应做镜像自检（模块可 import）"


def test_dockerfile_never_starts_the_viewer(dockerfile_text):
    """展示层只监听回环地址，不进入容器（design.md §7.6）。"""
    assert "--serve" not in dockerfile_text


def test_dockerignore_excludes_credentials_and_local_data():
    """.dockerignore 必须挡住凭据与本地数据；配置模板要留下。"""
    patterns = [
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    for forbidden in (".env", "config.yaml", "data/", ".git"):
        assert forbidden in patterns, f".dockerignore 没有排除 {forbidden}"
    assert "config.yaml.example" not in patterns, "配置模板不能被排除"


def test_runtime_stage_installs_runtime_dependencies_only(dockerfile_text):
    """运行镜像里不该出现 pytest：测试依赖属于 dev extra。"""
    builder, runtime = dockerfile_text.split("FROM python:3.11-slim AS runtime", 1)
    assert "httpx" in builder and "PyYAML" in builder and "Jinja2" in builder
    assert "pytest" not in runtime, "runtime 阶段不该装测试依赖"
