"""回归测试：CI/CD 流水线必须保持"测试不过就不产出镜像"这条性质。

本机没有 Docker，也没有 GitHub Actions，流水线无法真正跑起来；这里用静态断言
守住最容易退化的几处结构（specs/design.md §7.1、§7.5、§7.4）：

  - 触发条件：CI 全量跑、发布只在 main 与 v* 标签上跑；
  - 门禁关系：build `needs` test，publish `needs` build；
  - 依赖安装不许有 `|| true` 兜底；
  - `-o addopts=""`：否则 pyproject 的 -q 叠加成 -qq，看不到 "N passed"；
  - 凭据边界：只用 GITHUB_TOKEN，权限收敛；
  - 可追溯性：镜像标签含 7 位 sha、打印 digest、镜像名强制小写。

这些断言故意写得很"读文件"式：它们守的是 YAML 的结构事实，不是某个 action 的版本。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent  # sdd-reproduction/
WORKFLOWS = ROOT / ".github" / "workflows"
CI_FILE = WORKFLOWS / "workspace-ci.yml"
RELEASE_FILE = WORKFLOWS / "release-image.yml"

PROJECTS = ["sdd-daily-report", "kb-search", "brownfield-demo"]


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    """YAML 会把 `on:` 解析成布尔键 True，这里统一取出来。"""
    return workflow.get("on") or workflow.get(True) or {}


def _steps(workflow: dict, job: str) -> list[dict]:
    return workflow["jobs"][job]["steps"]


def _text_of(workflow: dict, job: str) -> str:
    return yaml.safe_dump(_steps(workflow, job), allow_unicode=True)


@pytest.fixture(scope="module")
def ci() -> dict:
    return _load(CI_FILE)


@pytest.fixture(scope="module")
def release() -> dict:
    return _load(RELEASE_FILE)


def test_both_workflows_exist_and_parse(ci, release):
    assert ci["name"] and release["name"]
    assert set(ci["jobs"]) >= {"test", "spec-governance"}
    assert set(release["jobs"]) >= {"test", "build", "publish"}


# ---------------------------------------------------------------------------
# workspace-ci.yml
# ---------------------------------------------------------------------------


def test_ci_runs_on_every_push_and_pull_request(ci):
    """每次推送与 PR 都要过一遍，不能只在打标签时验证。"""
    triggers = _triggers(ci)
    assert "push" in triggers
    assert "pull_request" in triggers


def test_ci_matrix_covers_all_three_projects(ci):
    """三个项目各有名为 tests 的包，必须分开跑（一次收集会模块名冲突）。"""
    matrix = ci["jobs"]["test"]["strategy"]["matrix"]["project"]
    assert matrix == PROJECTS


def test_ci_test_step_keeps_the_summary_line(ci):
    """`-o addopts=""` 让 "N passed" 汇总行还在；少了它日志里看不出跑了多少用例。"""
    assert '-o addopts=""' in _text_of(ci, "test")


def test_ci_installs_dependencies_without_swallowing_failures(ci):
    """`|| true` 会让"装不上依赖"也显示为绿 —— 那测试跑的根本不是这套依赖。"""
    text = _text_of(ci, "test")
    assert "pip install -e" in text
    assert "|| true" not in text


def test_ci_uploads_junit_report_as_artifact(ci):
    """失败时要能直接看到哪条用例挂了。"""
    text = _text_of(ci, "test")
    assert "--junitxml=" in text
    assert "upload-artifact" in text


def test_ci_uses_minimal_permissions_and_cancels_stale_runs(ci):
    assert ci["permissions"] == {"contents": "read"}
    assert ci["concurrency"]["cancel-in-progress"] is True


def test_ci_spec_governance_runs_all_gates(ci):
    """规范治理四件事 + 配置同步 + 镜像静态校验，缺一项就有角落没人守。"""
    text = _text_of(ci, "spec-governance")
    for gate in (
        "generate_tool_configs.py --check",
        "sdd_agents review",
        "sdd_agents team",
        "sdd_agents guard",
        "sdd_agents metrics",
        "verify_dockerfile.py",
    ):
        assert gate in text, f"CI 缺规范治理步骤：{gate}"


# ---------------------------------------------------------------------------
# release-image.yml
# ---------------------------------------------------------------------------


def test_release_only_runs_on_main_and_version_tags(release):
    """PR 不推镜像；标签只在 v* 上打。"""
    triggers = _triggers(release)
    push = triggers["push"]
    assert push["branches"] == ["main"]
    assert push["tags"] == ["v*"]
    assert "pull_request" not in triggers


def test_release_build_needs_passing_tests_and_publish_needs_build(release):
    """核心性质：测试红 ⇒ 不构建 ⇒ 不推送。这条不能退化成"同一个 job 里跑完所有事"。"""
    assert release["jobs"]["build"]["needs"] == "test"
    assert release["jobs"]["publish"]["needs"] == "build"


def test_release_pushes_to_ghcr_with_immutable_sha_tag(release):
    """标签要含 7 位提交哈希（可回滚），并打印 digest（部署锁 digest 而不是 latest）。"""
    text = _text_of(release, "publish")
    assert "ghcr.io" in text
    assert "cut -c1-7" in text
    assert "sha-" in text
    assert "digest" in text.lower()
    assert "docker push" in text


def test_release_lowercases_the_image_name(release):
    """仓库名 SDD-test 含大写，直接用会 invalid reference format。"""
    text = _text_of(release, "publish") + _text_of(release, "build")
    assert "tr '[:upper:]' '[:lower:]'" in text


def test_release_separates_login_from_push(release):
    """登录失败与推送被拒必须是两步，日志才分得清（proposal.md §3.3 v1.12）。"""
    names = [step.get("name", "") for step in _steps(release, "publish")]
    assert any("login" in n for n in names)
    assert any("push" in n for n in names)


def test_release_grants_packages_write_and_nothing_more(release):
    assert release["permissions"] == {"contents": "read", "packages": "write"}


def test_release_smoke_runs_offline_demo_mode(release):
    """冒烟只跑 --mock：不向真实飞书群或真实邮箱发信（design.md §7.5）。"""
    text = _text_of(release, "build")
    assert "--mock" in text
    assert "--serve" not in text
