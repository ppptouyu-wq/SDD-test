"""本机 Git 仓库的工程进展采集模块（Task 13，v1.8 新增）。

契约来源：specs/design.md §3.4（RepoProgress 数据模型与变更起点判定表）、
          specs/design.md §6.8（取数规则：只允许 git 与 metrics 能复现的数字）、
          specs/tasks.md Task 13、specs/contracts/data-models.md §2

取数对象是**日报所在的代码仓库本身**（本机 Git 历史），因此：

- 只用标准库 ``subprocess`` 调 ``git``，零新增依赖（与展示层的零依赖约束同一取向）；
- 失败**不参与** ``sources`` 四态（design.md §3.4）：本机没有 Git 仓库不是故障，
  只是"工程数据源"这一格填不上，日报其余部分不受影响；
- 规范覆盖率通过**注入的 callable** 取，采集层不 import ``sdd_agents``
  （那是本项目的工具，不是运行时依赖）。

变更起点判定（书里"找到受影响的最上游层级，从那里开始修改"的可执行化）：**只看路径，不看提交信息**。
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from shared.logger import get_logger
from shared.models import CollectResult, RepoProgress

logger = get_logger("collector.repo_progress")

GIT_TIMEOUT = 10  # 秒；本地 git 读历史，正常在毫秒级，超时即视为不可用

# 规范文件的判定口径（design.md §3.4 判定表）
SPEC_DIR = "specs/"
PROPOSAL_FILE = "specs/proposal.md"
DESIGN_FILE = "specs/design.md"
CONTRACTS_DIR = "specs/contracts/"
TASKS_FILE = "specs/tasks.md"

CHANGE_LAYER_PROPOSAL = "需求变更"
CHANGE_LAYER_DESIGN = "设计变更"
CHANGE_LAYER_TASKS = "任务变更"
CHANGE_LAYER_CODE = "缺陷修复"
CHANGE_LAYER_UNKNOWN = "未识别"

# 提交标题里的迭代版本号，如 v1.8 / v1.5.1
VERSION_PATTERN = re.compile(r"\bv(\d+)\.(\d+)(?:\.(\d+))?\b")

_RS = "\x1e"  # 提交之间的记录分隔符（git --pretty 的 %x1e）
_FS = "\x1f"  # 提交内部的字段分隔符（%x1f）


def _project_dir() -> Path:
    """本模块所在的工程目录（repo_path 未配置时的探测起点）。"""
    return Path(__file__).resolve().parent.parent


def find_repo_root(start: str | Path) -> Path | None:
    """定位 Git 仓库根目录；不在仓库内（或本机没有 git）时返回 None。"""
    try:
        completed = subprocess.run(  # noqa: S603 - 固定命令 + 绝对路径，无 shell
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(start),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # 没有 git / 不是仓库 / 超时
        logger.info("工程进展：未定位到 Git 仓库（%s）", exc)
        return None
    root = completed.stdout.strip()
    return Path(root) if root else None


def _read_log(root: Path, since: datetime, until: datetime) -> str:
    """读取时间窗内的提交与涉及文件。

    ``core.quotepath=false`` 是必需的：默认开启时 git 会把非 ASCII 路径转义成
    ``"\\346\\226\\207..."`` 形式，规范文件的中文名会被读成一串八进制。
    """
    args = [
        "git",
        "-c",
        "core.quotepath=false",
        "log",
        f"--since={since.strftime('%Y-%m-%d %H:%M:%S')}",
        f"--until={until.strftime('%Y-%m-%d %H:%M:%S')}",
        "--no-renames",
        "--name-only",
        f"--pretty=format:{_RS}%H{_FS}%s",
    ]
    completed = subprocess.run(  # noqa: S603 - 同上
        args,
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=GIT_TIMEOUT,
        check=True,
    )
    return completed.stdout


def _parse_log(output: str) -> list[tuple[str, list[str]]]:
    """把 git log 输出切成 [(提交标题, [涉及文件…]), …]。

    ``--pretty=format:`` 让每条提交的头部以 ``\\x1e`` 开头，之后依次是
    ``hash \\x1f subject``、空行、文件列表、空行。
    """
    commits: list[tuple[str, list[str]]] = []
    for chunk in output.split(_RS):
        if not chunk.strip():
            continue
        lines = chunk.splitlines()
        head = lines[0]
        subject = head.split(_FS, 1)[1].strip() if _FS in head else head.strip()
        files = [line.strip() for line in lines[1:] if line.strip()]
        commits.append((subject, files))
    return commits


def highest_version(subjects: list[str]) -> str:
    """取当日提交标题里最高的版本号；没有则返回空串（design.md §3.4）。"""
    best: tuple[int, int, int] | None = None
    best_text = ""
    for subject in subjects:
        for match in VERSION_PATTERN.finditer(subject):
            parts = (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))
            if best is None or parts > best:
                best = parts
                best_text = match.group(0)
    return best_text


def relative_to_project(path: str) -> str:
    """把 git 给出的路径归一成"相对项目根"的形式（design.md §3.4）。

    仓库把项目放在子目录里时（本仓库是 ``sdd-daily-report/``），git log 给出的路径是
    ``sdd-daily-report/specs/proposal.md``。判定表比的是 ``specs/...``，所以先截到
    ``specs`` 这一段；没有这一段的路径原样返回。
    """
    parts = [part for part in path.replace("\\", "/").split("/") if part not in ("", ".")]
    if "specs" in parts:
        return "/".join(parts[parts.index("specs") :])
    return "/".join(parts)


def decide_change_layer(files: list[str]) -> str:
    """按"受影响的最上游规范层"判定变更类型（design.md §3.4 判定表）。"""
    normalized = [relative_to_project(path) for path in files]
    if PROPOSAL_FILE in normalized:
        return CHANGE_LAYER_PROPOSAL
    if any(path == DESIGN_FILE or path.startswith(CONTRACTS_DIR) for path in normalized):
        return CHANGE_LAYER_DESIGN
    if TASKS_FILE in normalized:
        return CHANGE_LAYER_TASKS
    if files:
        return CHANGE_LAYER_CODE
    return CHANGE_LAYER_UNKNOWN


def _is_spec(path: str) -> bool:
    return relative_to_project(path).startswith(SPEC_DIR)


def collect(
    repo_path: str | Path = "",
    since: datetime | None = None,
    until: datetime | None = None,
    *,
    coverage: Callable[[], str] | None = None,
) -> CollectResult[RepoProgress]:
    """采集当日工程进展（Task 13）。

    :param repo_path: 仓库路径；空串表示由工程目录向上自动探测
    :param since: 起始时间（含）
    :param until: 结束时间（含）
    :param coverage: 规范覆盖率取数；未注入或抛错时该项为空串
    :return: 成功时 ``records`` 恰好一条；取不到仓库信息时 ``success=False``
    """
    if since is None or until is None:
        return CollectResult.fail("工程进展采集需要 since/until 时间窗")

    start = Path(repo_path) if str(repo_path).strip() else _project_dir()
    root = find_repo_root(start)
    if root is None:
        return CollectResult.fail("本机未找到 Git 仓库，工程进展板块不可用")

    try:
        output = _read_log(root, since, until)
    except (OSError, subprocess.SubprocessError) as exc:
        return CollectResult.fail(f"读取 Git 历史失败：{exc}")

    commits = _parse_log(output)
    subjects = [subject for subject, _ in commits]
    all_files = [path for _, files in commits for path in files]
    # 路径归一后再展示：日报上写 `specs/proposal.md` 比 `sdd-daily-report/specs/proposal.md` 干净
    spec_changed = sorted({relative_to_project(path) for path in all_files if _is_spec(path)})
    code_changed = any(not _is_spec(path) for path in all_files)
    same_commit = any(
        any(_is_spec(path) for path in files) and any(not _is_spec(path) for path in files)
        for _, files in commits
    )

    coverage_text = ""
    if coverage is not None:
        try:
            coverage_text = str(coverage() or "")
        except Exception as exc:  # noqa: BLE001 - 覆盖率是"锦上添花"，取不到不该拖垮采集
            logger.warning("工程进展：规范覆盖率取数失败（%s）", exc)

    progress = RepoProgress(
        commit_count=len(commits),
        version=highest_version(subjects),
        change_layer=decide_change_layer(all_files),
        spec_changed=spec_changed,
        code_changed=code_changed,
        same_commit=same_commit,
        spec_coverage=coverage_text,
    )
    logger.info(
        "工程进展采集完成 repo=%s commits=%d version=%s layer=%s same_commit=%s",
        root,
        progress.commit_count,
        progress.version or "-",
        progress.change_layer,
        progress.same_commit,
    )
    return CollectResult.ok([progress])
