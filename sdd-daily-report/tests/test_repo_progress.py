"""工程进展采集模块的单元测试（Task 13，v1.8 新增）。

对应 specs/tasks.md Task 13 的验收标准：
- 只要本地有 Git 仓库就能取到当日提交数与版本号；
- 变更起点按"最上游规范层"判定（只认路径，不认提交信息）；
- 规范与代码同一个提交时 same_commit 为真；
- 规范覆盖率由注入的 callable 提供，注入缺失或抛错都不得让采集失败；
- 取不到仓库信息时显式失败（success=False），绝不静默返回 0 条。

测试自己造一个**临时 Git 仓库**（`tmp_dir` 里 `git init`），不依赖本仓库的历史，
也不会因为本仓库当天有没有提交而飘。
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from collector import repo_progress

DAY = "2026-08-20"
SINCE = datetime(2026, 8, 20, 0, 0, 0)
UNTIL = datetime(2026, 8, 20, 23, 59, 59)

GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "SDD Test",
    "GIT_AUTHOR_EMAIL": "sdd-test@example.com",
    "GIT_COMMITTER_NAME": "SDD Test",
    "GIT_COMMITTER_EMAIL": "sdd-test@example.com",
}


def _git(repo: Path, *args: str, extra_env: dict[str, str] | None = None) -> None:
    env = {**os.environ, **GIT_IDENTITY, **(extra_env or {})}
    subprocess.run(  # noqa: S603 - 固定命令 + 测试自造的临时仓库
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        # 必须显式指定编码：中文提交信息是 UTF-8，而 Windows 的默认 locale 是 GBK，
        # 否则读取线程会抛 UnicodeDecodeError（本机实测确实会）。
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def _commit(repo: Path, subject: str, files: dict[str, str], *, day: str = DAY, at: str = "10:00") -> None:
    """提交若干文件（files 为 相对路径 → 内容）。"""
    for relative, content in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    stamp = f"{day}T{at}:00"
    _git(repo, "commit", "-m", subject, extra_env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})


@pytest.fixture
def repo(tmp_dir: Path) -> Iterator[Path]:
    path = tmp_dir / "sample-repo"
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    yield path


# ---------------------------------------------------------------- 仓库定位

def test_find_repo_root_locates_the_repository(repo: Path):
    root = repo_progress.find_repo_root(repo)

    assert root is not None
    assert root.name == "sample-repo"


def test_find_repo_root_returns_none_for_a_missing_directory(tmp_dir: Path):
    """本机没有 Git 仓库时返回 None —— 这是"可解释的缺失"，不是异常。"""
    assert repo_progress.find_repo_root(tmp_dir / "not-created") is None


# ---------------------------------------------------------------- 采集入口

def test_collect_requires_a_time_window(repo: Path):
    result = repo_progress.collect(repo)

    assert result.success is False
    assert "since/until" in (result.error_message or "")


def test_collect_fails_explicitly_when_there_is_no_repository(tmp_dir: Path):
    """取不到仓库信息必须显式失败，不能静默返回"0 次提交"。"""
    result = repo_progress.collect(tmp_dir / "not-created", SINCE, UNTIL)

    assert result.success is False
    assert result.records == []
    assert "Git 仓库" in (result.error_message or "")


def test_collect_counts_the_commits_of_the_day(repo: Path):
    _commit(repo, "feat: 日报三段式编排", {"main.py": "print('a')\n"})
    _commit(repo, "fix: 分页查询", {"shared/storage.py": "x = 1\n"}, at="14:05")

    result = repo_progress.collect(repo, SINCE, UNTIL)

    assert result.success is True
    assert len(result.records) == 1  # 成功时恰好一条
    progress = result.records[0]
    assert progress.commit_count == 2
    assert progress.code_changed is True
    assert progress.spec_changed == []


def test_commits_outside_the_window_are_ignored(repo: Path):
    _commit(repo, "feat(v1.7): 昨天的事", {"main.py": "print('old')\n"}, day="2026-08-19")

    result = repo_progress.collect(repo, SINCE, UNTIL)

    assert result.success is True
    progress = result.records[0]
    assert progress.commit_count == 0
    assert progress.version == ""  # 当天没有提交时版本号为空串
    assert progress.change_layer == repo_progress.CHANGE_LAYER_UNKNOWN


# ---------------------------------------------------------------- 变更起点判定

@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (["specs/proposal.md", "main.py"], repo_progress.CHANGE_LAYER_PROPOSAL),
        (["specs/proposal.md", "specs/design.md", "specs/tasks.md"], repo_progress.CHANGE_LAYER_PROPOSAL),
        (["specs/design.md"], repo_progress.CHANGE_LAYER_DESIGN),
        (["specs/contracts/data-models.md", "shared/models.py"], repo_progress.CHANGE_LAYER_DESIGN),
        (["specs/tasks.md", "tests/test_main.py"], repo_progress.CHANGE_LAYER_TASKS),
        (["main.py", "generator/template.py"], repo_progress.CHANGE_LAYER_CODE),
        ([], repo_progress.CHANGE_LAYER_UNKNOWN),
        # 项目嵌在子目录里时，git 给出的路径带前缀 —— 必须先归一（v1.8 真跑时踩到）
        (
            ["sdd-daily-report/specs/proposal.md", "sdd-daily-report/main.py"],
            repo_progress.CHANGE_LAYER_PROPOSAL,
        ),
        (
            ["app/specs/contracts/api-spec.yaml", "app/shared/models.py"],
            repo_progress.CHANGE_LAYER_DESIGN,
        ),
    ],
)
def test_change_layer_follows_the_most_upstream_spec_file(files: list[str], expected: str):
    """书里的"找到受影响的最上游层级，从那里开始修改"，落地成对路径的判定。"""
    assert repo_progress.decide_change_layer(files) == expected


def test_nested_project_layout_is_normalized_to_the_project_root(repo: Path):
    """项目放在子目录里（本仓库的真实布局）时，判定与展示都要截到项目根。

    未归一时的真实症状：`sdd-daily-report/specs/proposal.md` 比不过常量
    `specs/proposal.md`，于是需求变更被误判成"缺陷修复"、规范链一片空白
    （v1.8 真跑时踩到，`design.md` §3.4 因此补了"路径按项目根归一"一句）。
    """
    _commit(
        repo,
        "feat(v1.8): 需求变更，规范与代码同一个提交",
        {
            "sdd-daily-report/specs/proposal.md": "# 需求\n",
            "sdd-daily-report/specs/design.md": "# 设计\n",
            "sdd-daily-report/collector/repo_progress.py": "print('x')\n",
        },
    )

    progress = repo_progress.collect(repo, SINCE, UNTIL).records[0]

    assert progress.change_layer == repo_progress.CHANGE_LAYER_PROPOSAL
    assert progress.spec_changed == ["specs/design.md", "specs/proposal.md"]
    assert progress.code_changed is True
    assert progress.same_commit is True


def test_change_layer_is_decided_by_paths_not_by_the_commit_message(repo: Path):
    """提交信息里写着"需求变更"，但只改了代码 —— 判定必须是缺陷修复。"""
    _commit(repo, "feat: 需求变更，其实只动了代码", {"main.py": "print('x')\n"})

    progress = repo_progress.collect(repo, SINCE, UNTIL).records[0]

    assert progress.change_layer == repo_progress.CHANGE_LAYER_CODE


def test_collect_reports_design_change_when_specs_and_code_land_together(repo: Path):
    _commit(
        repo,
        "feat(v1.8): 新增工程进展板块",
        {"specs/design.md": "# 设计\n", "collector/repo_progress.py": "# code\n"},
    )

    progress = repo_progress.collect(repo, SINCE, UNTIL).records[0]

    assert progress.change_layer == repo_progress.CHANGE_LAYER_DESIGN
    assert progress.spec_changed == ["specs/design.md"]
    assert progress.code_changed is True
    assert progress.same_commit is True  # 规范与代码同一个提交（书图 7-8 的闭环要求）


def test_same_commit_is_false_when_the_spec_landed_separately(repo: Path):
    _commit(repo, "docs: 先改规范", {"specs/design.md": "# 设计\n"}, at="09:00")
    _commit(repo, "feat: 再改代码", {"main.py": "print('x')\n"}, at="11:00")

    progress = repo_progress.collect(repo, SINCE, UNTIL).records[0]

    assert progress.commit_count == 2
    assert progress.same_commit is False


def test_chinese_spec_paths_survive_the_git_log(repo: Path):
    """`core.quotepath=false` 的回归保护：否则中文路径会变成八进制转义。"""
    _commit(repo, "docs: 中文规范文件", {"specs/接口契约.md": "# 契约\n"})

    progress = repo_progress.collect(repo, SINCE, UNTIL).records[0]

    assert progress.spec_changed == ["specs/接口契约.md"]


# ---------------------------------------------------------------- 版本号

def test_highest_version_picks_the_largest_number():
    subjects = ["fix: v1.5.1 收尾", "feat(v1.8): 工程进展", "chore: 没有版本号"]

    assert repo_progress.highest_version(subjects) == "v1.8"


def test_highest_version_compares_numerically_not_lexically():
    assert repo_progress.highest_version(["feat(v1.10.0): x", "feat(v1.9): y"]) == "v1.10.0"


def test_highest_version_is_empty_without_any_version():
    assert repo_progress.highest_version(["chore: 无版本"]) == ""


def test_version_comes_from_the_commit_subject(repo: Path):
    _commit(repo, "feat(v1.8): 新增工程进展板块", {"main.py": "print('x')\n"})

    progress = repo_progress.collect(repo, SINCE, UNTIL).records[0]

    assert progress.version == "v1.8"


# ---------------------------------------------------------------- 覆盖率注入

def test_coverage_comes_from_the_injected_callable(repo: Path):
    _commit(repo, "feat: x", {"main.py": "print('x')\n"})

    progress = repo_progress.collect(repo, SINCE, UNTIL, coverage=lambda: "97/98 = 99.0%").records[0]

    assert progress.spec_coverage == "97/98 = 99.0%"


def test_coverage_failure_degrades_instead_of_breaking_the_collection(repo: Path):
    """覆盖率是"锦上添花"：取不到就留空串，日报其余部分不受影响。"""
    _commit(repo, "feat: x", {"main.py": "print('x')\n"})

    def _boom() -> str:
        raise RuntimeError("metrics 不可用")

    result = repo_progress.collect(repo, SINCE, UNTIL, coverage=_boom)

    assert result.success is True
    assert result.records[0].spec_coverage == ""


def test_coverage_is_empty_when_not_injected(repo: Path):
    _commit(repo, "feat: x", {"main.py": "print('x')\n"})

    assert repo_progress.collect(repo, SINCE, UNTIL).records[0].spec_coverage == ""


# ---------------------------------------------------------------- git 输出解析

def test_parse_log_splits_commits_and_files():
    output = (
        "\x1eabc123\x1ffeat: 第一件事\n\nmain.py\nshared/models.py\n\n"
        "\x1edef456\x1ffix: 第二件事\n\nspecs/design.md\n\n"
    )

    commits = repo_progress._parse_log(output)

    assert commits == [
        ("feat: 第一件事", ["main.py", "shared/models.py"]),
        ("fix: 第二件事", ["specs/design.md"]),
    ]


def test_parse_log_ignores_empty_output():
    assert repo_progress._parse_log("") == []
