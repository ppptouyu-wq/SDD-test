"""交付流水线采集模块（Task 7 的 v1.13 变更）。

契约来源：specs/design.md §3.5（PipelineStatus 与取数口径）、§6.7(6)（板块位置）、
          specs/proposal.md §3.1 v1.13 三条 / §3.3 v1.13 一条

**只读仓库里的文件与 Git 事实**：`.github/workflows/*.yml`、`Dockerfile`、`git tag`。
刻意不联网、不调 Actions API：日报进程手里的令牌没有 `actions:read`
（实测打 Actions 日志接口返回 403），所以 `last_run_status` 一律留空，
渲染成"未采集"。**空 ≠ 失败，也 ≠ 跑通** —— 这一条是 v1.13 的底线：
日报里出现的每个状态都必须能被收件人当场复核，否则就是自吹。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from collector.repo_progress import find_repo_root
from shared.logger import get_logger
from shared.models import PipelineStatus

logger = get_logger("collector.pipeline")

GIT_TIMEOUT = 10  # 秒；只读本地历史，正常在毫秒级

_FROM_RE = re.compile(r"^\s*FROM\s+\S+", re.IGNORECASE | re.MULTILINE)
_USER_RE = re.compile(r"^\s*USER\s+(\S+)", re.IGNORECASE | re.MULTILINE)

# 镜像里以 root 运行的写法
_ROOT_USERS = {"root", "0", "0:0"}

# 可能的 Dockerfile 位置（按优先级）：项目自己的目录在前，仓库根在后
_DOCKERFILE_CANDIDATES = ("sdd-daily-report/Dockerfile", "Dockerfile")


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def workflow_files(root: Path) -> list[str]:
    """仓库里所有工作流文件（仓库根相对路径），按名字排序。

    同时看两处：仓库根（工作区级 CI）与 `sdd-daily-report/`（项目级）。
    两处都没有时返回空列表 —— 那不是故障，只是这个仓库还没有流水线。
    """
    found: list[Path] = []
    for base in (root / ".github" / "workflows", root / "sdd-daily-report" / ".github" / "workflows"):
        if not base.is_dir():
            continue
        found.extend(
            p for p in base.iterdir() if p.is_file() and p.suffix in {".yml", ".yaml"}
        )
    return sorted(_rel(p, root) for p in found)


def _dockerfile(root: Path) -> Path | None:
    for candidate in _DOCKERFILE_CANDIDATES:
        path = root / candidate
        if path.is_file():
            return path
    return None


def image_facts(text: str) -> tuple[int, bool, bool]:
    """从 Dockerfile 文本里读三项事实：(阶段数, 是否非 root, 是否有 HEALTHCHECK)。"""
    stages = len(_FROM_RE.findall(text))
    users = [u.strip() for u in _USER_RE.findall(text)]
    nonroot = bool(users) and not any(u in _ROOT_USERS for u in users)
    return stages, nonroot, "HEALTHCHECK" in text.upper()


def _latest_tag(root: Path) -> str:
    """最新的版本标签；按版本号排序（`--sort=-v:refname`），不是按创建时间。"""
    try:
        completed = subprocess.run(  # noqa: S603 - 固定命令 + 绝对路径，无 shell
            ["git", "tag", "--list", "v*", "--sort=-v:refname"],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.info("交付流水线：读取 git tag 失败（%s）", exc)
        return ""
    tags = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return tags[0] if tags else ""


def collect(repo_path: str | Path = "") -> PipelineStatus | None:
    """采集交付流水线现状。

    :param repo_path: 仓库路径；空串表示由工程目录向上自动探测
    :return: 成功时返回 PipelineStatus；本机不是 Git 仓库、或仓库里
        工作流/镜像/标签三者都没有时返回 None（邮件渲染中性空态）
    """
    start = Path(repo_path) if str(repo_path).strip() else Path(__file__).resolve().parent.parent
    root = find_repo_root(start)
    if root is None:
        return None

    workflows = workflow_files(root)
    texts = []
    for name in workflows:
        path = root / name
        try:
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:  # 读不到就当没这个工作流，但要说一声
            logger.warning("交付流水线：读取工作流失败 %s（%s）", name, exc)
    joined = "\n".join(texts)

    dockerfile = _dockerfile(root)
    stages, nonroot, healthcheck = (0, False, False)
    if dockerfile is not None:
        stages, nonroot, healthcheck = image_facts(
            dockerfile.read_text(encoding="utf-8", errors="replace")
        )

    latest_tag = _latest_tag(root)

    if not workflows and dockerfile is None and not latest_tag:
        logger.info("交付流水线：仓库里没有工作流、镜像与标签，视为未记录")
        return None

    status = PipelineStatus(
        ci_workflows=workflows,
        release_to_registry="ghcr.io" in joined,
        image_stages=stages,
        image_nonroot=nonroot,
        image_healthcheck=healthcheck,
        latest_tag=latest_tag,
        # 令牌没有 actions:read（实测 403）→ 留空，渲染成"未采集"。不许猜。
        last_run_status="",
    )
    logger.info(
        "交付流水线采集完成 workflows=%d release=%s stages=%d nonroot=%s healthcheck=%s tag=%s",
        len(status.ci_workflows),
        status.release_to_registry,
        status.image_stages,
        status.image_nonroot,
        status.image_healthcheck,
        status.latest_tag or "-",
    )
    return status
