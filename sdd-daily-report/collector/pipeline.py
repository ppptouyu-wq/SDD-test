"""交付流水线采集模块（specs/tasks.md Task 15；v1.13 起有、v1.14 定稿）。

契约来源：specs/design.md §3.5（PipelineStatus 与取数口径）、§6.7(6)（板块位置）、
          specs/proposal.md §3.1 的 v1.13/v1.14 验收项

三类取数，边界清清楚楚：
  1. **仓库文件**：`.github/workflows/*.yml`、`Dockerfile`；
  2. **本地 Git 事实**：`git tag`（只作远端标签的回退）；
  3. **GitHub 公开接口**（v1.14）：默认分支、最新提交的 Check Runs、远端 `v*` 标签 ——
     这些接口**匿名可读**，不需要 `actions:read`（v1.13 说"取不到"是查错了接口：
     日志接口要权限，结论接口不要）。
**底线不变**：每一项都要能被收件人当场复核。读不到就留空、渲染成"未采集"，
绝不把"文件都在"说成"流水线已跑通"，也绝不用本地状态猜一次线上运行的结果。
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import httpx

from collector.repo_progress import find_repo_root
from shared.logger import get_logger
from shared.models import PipelineStatus

logger = get_logger("collector.pipeline")

GIT_TIMEOUT = 10  # 秒；只读本地历史，正常在毫秒级
GITHUB_API = "https://api.github.com"
HTTP_TIMEOUT = 8.0  # 秒；单次接口取数的上限，超时就当作"读不到"

_FROM_RE = re.compile(r"^\s*FROM\s+\S+", re.IGNORECASE | re.MULTILINE)
_USER_RE = re.compile(r"^\s*USER\s+(\S+)", re.IGNORECASE | re.MULTILINE)
_VTAG_RE = re.compile(r"^v(\d+)(?:\.(\d+))?(?:\.(\d+))?$")

# 镜像里以 root 运行的写法
_ROOT_USERS = {"root", "0", "0:0"}

# 这些结论算"没有红灯"：跳过的可选检查不该把整条流水线拖成失败
_PASS_CONCLUSIONS = {"success", "neutral", "skipped"}

# 可能的 Dockerfile 位置（按优先级）：项目自己的目录在前，仓库根在后
_DOCKERFILE_CANDIDATES = ("sdd-daily-report/Dockerfile", "Dockerfile")

# 取数函数签名：给他一个 URL，回来一个解析好的 JSON（对象或数组）；读不到就是 None
HttpGet = Callable[[str], "object | None"]



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


def _http_get_json(url: str, *, token: str | None = None, timeout: float = HTTP_TIMEOUT) -> object | None:
    """默认取数实现：一次 GET，拿 JSON；任何异常都收敛成 None（读不到 ≠ 崩掉）。

    返回类型保持 `object`：GitHub 有些接口回对象（仓库元信息、Check Runs），
    有些回数组（`/tags`），别为了统一形状而在这一层丢掉数组。
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "sdd-daily-report/1.14",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:  # 超时、DNS、TLS、连接被拒……一律按"读不到"
        logger.info("交付流水线：请求 %s 失败（%s）", url, exc)
        return None
    if response.status_code != 200:
        # 403/429 是限流、404 是仓库或分支不对：都不该让日报失败，只让那一行留空
        logger.info("交付流水线：%s 返回 HTTP %s，按未采集处理", url, response.status_code)
        return None
    try:
        return response.json()
    except ValueError:
        logger.info("交付流水线：%s 返回的不是 JSON，按未采集处理", url)
        return None


def check_runs_summary(payload: object) -> tuple[str, str]:
    """把 Check Runs 接口的响应聚合成 (结论串, 提交哈希前 7 位)；纯函数，便于确定性测试。

    口径（design.md §3.5，proposal.md §3.1 v1.14 第 3 条）：
      无检查项            → ("", "")           没有检查项就没有结论，不写"成功"
      有未完成的检查项    → "进行中（N/M 项未完成）"  —— 不与"成功"混同
      已完成里有红灯      → "失败（K/M 项未通过）"
      已完成且全绿        → "成功（M 项全部通过）"
    """
    runs = payload.get("check_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list) or not runs:
        return "", ""
    total = len(runs)
    first = runs[0] if isinstance(runs[0], dict) else {}
    sha = str(first.get("head_sha") or "")[:7]
    pending = [run for run in runs if run.get("status") != "completed"]
    if pending:
        return f"进行中（{len(pending)}/{total} 项未完成）", sha
    failed = [run for run in runs if run.get("conclusion") not in _PASS_CONCLUSIONS]
    if failed:
        return f"失败（{len(failed)}/{total} 项未通过）", sha
    return f"成功（{total} 项全部通过）", sha


def latest_remote_tag(payload: object) -> str:
    """从 `/repos/{slug}/tags` 的响应里挑最新的 `v*` 版本标签；纯函数。

    该接口回的是**数组**（`[{"name": "v1.14.0", ...}, ...]`）。按版本号逐段比较，
    不用字典序——字典序会把 `v1.9.0` 排在 `v1.10.0` 前面，正是 tag 排序的经典坑。
    """
    items: object = payload
    if isinstance(payload, dict):  # 兼容被包了一层的调用方
        items = payload.get("tags")
    if not isinstance(items, list):
        return ""
    keyed: list[tuple[tuple[int, int, int], str]] = []
    for item in items:
        name = str(item.get("name") or "") if isinstance(item, dict) else ""
        match = _VTAG_RE.match(name)
        if match:
            keyed.append((tuple(int(part or 0) for part in match.groups()), name))
    return max(keyed)[1] if keyed else ""


def collect(
    repo_path: str | Path = "",
    *,
    slug: str = "",
    token: str | None = None,
    api_base: str = GITHUB_API,
    timeout: float = HTTP_TIMEOUT,
    http_get: HttpGet | None = None,
) -> PipelineStatus | None:
    """采集交付流水线现状。

    :param repo_path: 仓库路径；空串表示由工程目录向上自动探测
    :param slug: `owner/repo`；**空串表示不联网**（离线与测试场景的确定性保证）
    :param token: 可选令牌，只用于提高限流额度，不是取到结论的前提
    :param api_base: GitHub API 根地址（测试可指向假地址）
    :param timeout: 单次接口取数超时（秒）
    :param http_get: 注入的取数函数，签名 `(url) -> dict | None`；测试用它避免真网络
    :return: 成功时返回 PipelineStatus；本机不是 Git 仓库、或仓库里
        工作流/镜像/标签/运行结论全都没有时返回 None（邮件渲染中性空态）
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

    remote_tag = ""
    run_status, run_commit = "", ""
    if slug.strip():
        fetch: HttpGet = http_get or (
            lambda url: _http_get_json(url, token=token, timeout=timeout)
        )
        base = api_base.rstrip("/")
        repo_meta = fetch(f"{base}/repos/{slug}")
        branch = "main"
        if isinstance(repo_meta, dict):
            branch = str(repo_meta.get("default_branch") or "main")
        remote_tag = latest_remote_tag(fetch(f"{base}/repos/{slug}/tags?per_page=100"))
        run_status, run_commit = check_runs_summary(
            fetch(f"{base}/repos/{slug}/commits/{branch}/check-runs?per_page=100")
        )

    latest_tag = remote_tag or _latest_tag(root)

    if not workflows and dockerfile is None and not latest_tag and not run_status:
        logger.info("交付流水线：仓库里没有工作流、镜像、标签与运行结论，视为未记录")
        return None

    status = PipelineStatus(
        ci_workflows=workflows,
        release_to_registry="ghcr.io" in joined,
        image_stages=stages,
        image_nonroot=nonroot,
        image_healthcheck=healthcheck,
        latest_tag=latest_tag,
        # 结论只能来自接口；读不到就留空，渲染成"未采集"。不许猜。
        last_run_status=run_status,
        last_run_commit=run_commit,
    )
    logger.info(
        "交付流水线采集完成 workflows=%d release=%s stages=%d nonroot=%s healthcheck=%s "
        "tag=%s run=%s",
        len(status.ci_workflows),
        status.release_to_registry,
        status.image_stages,
        status.image_nonroot,
        status.image_healthcheck,
        status.latest_tag or "-",
        (status.last_run_status or "-") + (f"@{status.last_run_commit}" if status.last_run_commit else ""),
    )
    return status
