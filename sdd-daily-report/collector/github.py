"""GitHub 采集模块。

契约来源：specs/design.md §4.1 采集层接口契约 / specs/tasks.md Task 3
    github.collect(repos, since, until) -> CollectResult[CommitRecord]

验收标准（tasks.md Task 3）：
- [x] collect() 函数签名符合 design.md §4.1 的接口定义
- [x] 返回的每个 CommitRecord 包含全部7个字段且类型正确
- [x] 支持分页查询（GitHub API 默认每页30条）
- [x] API 超时重试3次（间隔5s），若仍失败则标记数据获取失败
- [x] GitHub API 限流（HTTP 403）时，等待 reset 时间后重试
- [x] 使用 Mock 数据的单元测试全部通过

职责边界（design.md §2）：只负责"从外部 API 获取原始数据"，
不做数据格式化、不做去重判断、不做推送。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

import httpx

from shared.config import GitHubConfig
from shared.errors import CollectorError
from shared.logger import get_logger
from shared.models import CollectResult, CommitRecord

logger = get_logger("collector.github")

PER_PAGE = 30  # GitHub API 默认每页 30 条


def _parse_timestamp(value: str) -> datetime:
    """解析 GitHub 的 ISO 8601 时间戳，如 2026-08-20T09:30:00Z。"""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _wait_until_reset(response: httpx.Response, sleep: Callable[[float], None]) -> None:
    """限流（HTTP 403 / 429）时等待 reset。"""
    reset = response.headers.get("X-RateLimit-Reset")
    remaining = response.headers.get("X-RateLimit-Remaining")
    wait_for = 1.0
    if reset and remaining == "0":
        try:
            wait_for = max(1.0, float(reset) - time.time())
        except ValueError:  # pragma: no cover - defensive
            wait_for = 1.0
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            wait_for = max(wait_for, float(retry_after))
        except ValueError:  # pragma: no cover - defensive
            pass
    logger.error(
        "GitHub API 触发限流，等待后重试",
        extra={"source": "github", "wait_seconds": round(wait_for, 3)},
    )
    sleep(wait_for)


def _collect_repo(
    client: httpx.Client,
    repo: str,
    since: datetime,
    until: datetime,
    *,
    max_retries: int,
    retry_interval: float,
    sleep: Callable[[float], None],
) -> list[CommitRecord]:
    """采集单个仓库的全部 Commit（含分页）。"""
    records: list[CommitRecord] = []
    base_params = _base_params(since, until)
    url: str | None = f"/repos/{repo}/commits"
    params: dict[str, object] | None = dict(base_params)
    page_count = 0

    while url:
        page_count += 1

        def _request() -> httpx.Response:
            response = client.get(url, params=params)
            if response.status_code in (403, 429):
                _wait_until_reset(response, sleep)
                raise CollectorError(
                    f"GitHub API 限流（HTTP {response.status_code}）",
                    source=f"github:{repo}",
                )
            if response.status_code >= 400:
                raise CollectorError(
                    f"GitHub API 返回 HTTP {response.status_code}",
                    source=f"github:{repo}",
                )
            return response

        last_error: Exception | None = None
        response: httpx.Response | None = None
        for attempt in range(max_retries + 1):
            try:
                response = _request()
                break
            except (httpx.HTTPError, CollectorError) as exc:
                last_error = exc
                if attempt < max_retries:
                    logger.error(
                        "GitHub 采集失败，准备重试",
                        extra={
                            "source": f"github:{repo}",
                            "attempt": attempt + 1,
                            "max_retries": max_retries,
                            "error": str(exc),
                        },
                    )
                    if retry_interval:
                        sleep(retry_interval)
        if response is None:
            raise CollectorError(
                f"GitHub API 重试 {max_retries} 次后仍失败：{last_error}",
                source=f"github:{repo}",
            )

        payload = response.json()
        if not isinstance(payload, list):
            raise CollectorError(
                f"GitHub API 返回了非预期结构：{type(payload).__name__}",
                source=f"github:{repo}",
            )
        for item in payload:
            commit = item.get("commit", {})
            stats = item.get("stats") or {}
            files = item.get("files")

            # GitHub 的 /commits 列表接口不返回 stats / files（只有单条 commit
            # 详情接口才返回）。若直接用列表里的缺省值，净增删行数与文件数会
            # 恒为 0，无法满足 design.md §3 的 CommitRecord 契约，因此这里按需
            # 回查一次详情。回查失败不影响该条记录的其余字段，只降级为 0。
            if not stats or files is None:
                sha = str(item.get("sha") or "")
                detail = _fetch_commit_detail(client, repo, sha) if sha else None
                if isinstance(detail, dict):
                    stats = detail.get("stats") or stats
                    if detail.get("files") is not None:
                        files = detail["files"]

            records.append(
                CommitRecord(
                    author=str(
                        (commit.get("author") or {}).get("name")
                        or (item.get("author") or {}).get("login")
                        or "unknown"
                    ),
                    message=str(commit.get("message", "")),
                    timestamp=_parse_timestamp(str(commit.get("author", {}).get("date", ""))),
                    repo=repo,
                    additions=int(stats.get("additions", 0)),
                    deletions=int(stats.get("deletions", 0)),
                    files_changed=int(len(files or [])),
                )
            )

        url, params = _next_page(response, repo, params)
        # 翻页时沿用原始的 since/until/per_page 过滤条件，只让 page 变化
        if url and params is None:
            url = str(httpx.URL(url).copy_merge_params(base_params))

    logger.info(
        "GitHub 采集完成",
        extra={"source": f"github:{repo}", "count": len(records), "pages": page_count},
    )
    return records


def _fetch_commit_detail(
    client: httpx.Client, repo: str, sha: str
) -> dict | None:
    """回查单条 commit 详情以取回 stats / files。

    列表接口不返回这两个字段。此处失败只记日志并返回 None，
    由调用方降级为 0，绝不因回查失败而丢掉整条提交记录（优雅降级）。
    """
    try:
        response = client.get(f"/repos/{repo}/commits/{sha}")
    except httpx.HTTPError as exc:
        logger.error(
            "回查 commit 详情失败，统计字段降级为 0",
            extra={"source": f"github:{repo}", "sha": sha[:8], "error": str(exc)},
        )
        return None
    if response.status_code >= 400:
        logger.error(
            "回查 commit 详情返回错误，统计字段降级为 0",
            extra={"source": f"github:{repo}", "sha": sha[:8], "status": response.status_code},
        )
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _base_params(since: datetime, until: datetime) -> dict[str, object]:
    """首个请求与后续翻页共用的过滤参数。"""
    return {
        "since": since.astimezone(timezone.utc).isoformat(),
        "until": until.astimezone(timezone.utc).isoformat(),
        "per_page": PER_PAGE,
    }


def _next_page(
    response: httpx.Response,
    repo: str,
    current_params: dict[str, object] | None,
) -> tuple[str | None, dict[str, object] | None]:
    """从 Link 头解析下一页。

    返回 (next_url, params)。翻页时 params 置为 None，由调用方把原始过滤条件
    合并进 next_url，确保 since/until/per_page 在翻页后依然生效。
    """
    link = response.headers.get("Link", "")
    for part in link.split(","):
        if 'rel="next"' in part:
            url = part.split(";")[0].strip().strip("<>")
            if not url:
                break
            return url, None
    return None, None


def collect(
    repos: list[str],
    since: datetime,
    until: datetime,
    *,
    config: GitHubConfig,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> CollectResult[CommitRecord]:
    """采集指定仓库在 [since, until] 内的 Commit 记录。

    依据 ADR-003，返回 CollectResult 以便显式区分"无数据"与"采集失败"。
    单个仓库失败不会阻断其他仓库（优雅降级）。
    """
    if not repos:
        return CollectResult.ok([])

    owns_client = client is None
    if client is None:
        headers = {"Accept": "application/vnd.github+json"}
        if config.token:
            headers["Authorization"] = f"Bearer {config.token}"
        client = httpx.Client(base_url=config.api_base, headers=headers, timeout=config.timeout)

    all_records: list[CommitRecord] = []
    failures: list[str] = []

    try:
        for repo in repos:
            try:
                all_records.extend(
                    _collect_repo(
                        client,
                        repo,
                        since,
                        until,
                        max_retries=config.max_retries,
                        retry_interval=config.retry_interval,
                        sleep=sleep,
                    )
                )
            except CollectorError as exc:
                failures.append(f"{repo}: {exc.message}")
                logger.error(
                    "仓库采集失败，跳过该仓库",
                    extra={"source": f"github:{repo}", "error": exc.message},
                )
    finally:
        if owns_client:
            client.close()

    if failures and not all_records:
        return CollectResult.fail("；".join(failures))
    if failures:
        # 部分成功：记录失败信息，但仍返回已采集到的数据
        logger.error(
            "部分仓库采集失败",
            extra={"source": "github", "failed": len(failures), "ok_records": len(all_records)},
        )
    return CollectResult.ok(all_records)
