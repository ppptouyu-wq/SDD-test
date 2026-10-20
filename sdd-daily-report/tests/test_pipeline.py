"""交付流水线采集与渲染（v1.13；specs/design.md §3.5、§6.7(6)）。

只读仓库文件与 Git 事实：工作流文件名、`Dockerfile` 的 `FROM`/`USER`/`HEALTHCHECK`、`git tag`。
本文件重点守两条底线：
  1. 取不到就说"未采集"，**绝不**把"文件都在"写成"流水线已跑通"；
  2. 仓库里什么都没有时是 None（中性空态），不是采集失败。
"""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

from collector import pipeline as pipeline_collector
from generator import formatter, template
from shared.config import Member
from shared.models import PipelineStatus

REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # sdd-reproduction/
DAY = date(2026, 8, 20)


# --------------------------------------------------------------- 文件与镜像事实


def test_workflow_files_finds_both_levels():
    """仓库根（工作区级 CI）与 `sdd-daily-report/`（项目级）的工作流都要收进来。"""
    names = pipeline_collector.workflow_files(REPO_ROOT)

    assert ".github/workflows/workspace-ci.yml" in names
    assert ".github/workflows/release-image.yml" in names
    assert any(n.endswith("spec-check.yml") for n in names)
    assert names == sorted(names), "结果要排序，否则日报每次行序都可能不同"


def test_image_facts_read_multistage_nonroot_healthcheck():
    text = (
        "FROM python:3.11-slim AS builder\n"
        "FROM python:3.11-slim AS runtime\n"
        "RUN useradd --uid 10001 app\n"
        "USER app\n"
        "HEALTHCHECK CMD python -c 'import main'\n"
    )
    stages, nonroot, healthcheck = pipeline_collector.image_facts(text)

    assert stages == 2
    assert nonroot is True
    assert healthcheck is True


def test_image_facts_ignore_root_user():
    """`USER root` / 没有 USER 都算"以 root 运行"，不能因为写了 USER 就算非 root。"""
    assert pipeline_collector.image_facts("FROM python:3.11-slim\nUSER root\n") == (1, False, False)
    assert pipeline_collector.image_facts("FROM python:3.11-slim\n") == (1, False, False)


# --------------------------------------------------------------- 真实仓库采集


def test_collect_reads_the_real_repository():
    status = pipeline_collector.collect("")

    assert isinstance(status, PipelineStatus)
    assert status.ci_workflows, "本仓库有工作流，不该为空"
    assert status.release_to_registry is True, "release-image.yml 里有 ghcr.io"
    assert status.image_stages >= 2, "Dockerfile 是多阶段构建"
    assert status.image_nonroot is True
    assert status.image_healthcheck is True
    assert status.latest_tag.startswith("v")


def test_collect_never_fabricates_the_run_status():
    """令牌没有 actions:read（实测 403）→ 一律留空，由渲染层写"未采集"。"""
    status = pipeline_collector.collect("")

    assert status is not None
    assert status.last_run_status == ""


def test_collect_returns_none_when_repository_has_no_pipeline(tmp_dir):
    """空仓库（有 .git，但没有工作流/镜像/标签）→ None，渲染中性空态。"""
    root = tmp_dir / "bare"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)

    assert pipeline_collector.collect(root) is None


def test_collect_returns_none_outside_a_git_repository():
    """不在任何 Git 仓库里 → None（这一条用系统临时目录，它不在本仓库内部）。"""
    import tempfile

    assert pipeline_collector.collect(Path(tempfile.gettempdir())) is None


# --------------------------------------------------------------- 邮件渲染


def test_email_shows_the_pipeline_block():
    """板块要出现在「今日工程进展」之后，并带上阶段链与四行现状。"""
    members = formatter.aggregate([Member(name="张三", github="zhangsan", lark="zhangsan@company.com")])
    status = PipelineStatus(
        ci_workflows=[".github/workflows/workspace-ci.yml"],
        release_to_registry=True,
        image_stages=2,
        image_nonroot=True,
        image_healthcheck=True,
        latest_tag="v1.12.0",
        last_run_status="",
    )

    report = formatter.generate(members, DAY, "平台研发组", pipeline=status)
    html = report.html

    assert template.SECTION_PIPELINE in html
    assert "测试门禁" in html and "部署（本期不做）" in html
    assert "2 阶段" in html and "非 root" in html and "HEALTHCHECK" in html
    assert "推送到 ghcr.io" in html
    assert "v1.12.0" in html
    # 取不到首跑结论时写"未采集"，不写"已跑通"
    assert template.PIPELINE_RUN_UNKNOWN in html
    assert "已跑通" not in html
    # 位置：工程进展之后
    assert html.index(template.SECTION_PROGRESS) < html.index(template.SECTION_PIPELINE)


def test_email_shows_neutral_state_without_pipeline():
    """仓库里没有流水线时是中性空态，不写成采集失败。"""
    members = formatter.aggregate([Member(name="张三", github="zhangsan", lark="zhangsan@company.com")])
    report = formatter.generate(members, DAY, "平台研发组")

    assert template.PIPELINE_EMPTY in report.html
    assert "采集失败" not in report.html.split(template.SECTION_PIPELINE)[1][:400]


def test_markdown_does_not_get_the_pipeline_block():
    """该板块只在邮件渠道出现；Markdown（落库原件/飞书推送）保持不变。"""
    members = formatter.aggregate([Member(name="张三", github="zhangsan", lark="zhangsan@company.com")])
    report = formatter.generate(
        members, DAY, "平台研发组", pipeline=PipelineStatus(ci_workflows=["a.yml"])
    )

    assert template.SECTION_PIPELINE not in report.markdown
