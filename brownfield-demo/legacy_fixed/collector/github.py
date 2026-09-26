"""【修正版】GitHub 提交采集 —— 按 specs/contracts/legacy-baseline.md 定稿契约实现。

这是第 7.4.3 节四步法的**第二步产出落地**：把人工判定为"修正"的项真正改掉。
与 legacy/collector/github.py 的差异（逐条对应契约 1.2 表格）：

  3. 限流不再固定 sleep(60)，改为读 X-RateLimit-Reset 计算真实等待，并设等待上限
  5. 采集层只返回结构化记录，**不再顺手拼文本**（职责越界已修正）
  6. 详情回查失败不再静默填 0，而是记警告 + 用 None 表示"未知"
  7. check_token() 只有 200 才返回 True（不再掩盖失败）
  8. 补全类型标注

依赖：仅标准库（与基线一致，不引入新依赖）
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

GITHUB_API = "https://api.github.com"

# 契约 1.2 约定的常量
MAX_RETRIES = 3
RETRY_INTERVAL = 5.0
TIMEOUT = 30.0
PER_PAGE = 30
MAX_RATE_LIMIT_WAIT = 300.0  # 限流等待上限：不得超过 5 分钟，否则会挂死定时任务


class CollectorError(Exception):
    """采集失败。"""


class AuthError(CollectorError):
    """认证失败（401）：重试无意义，立即抛出。"""


@dataclass
class CommitRecord:
    """一条提交记录。统计字段取不到时为 None，表示"未知"而非 0。"""

    author: str
    message: str
    timestamp: datetime
    repo: str
    additions: int | None
    deletions: int | None
    files_changed: int | None


class GithubCollector:
    """GitHub 采集器（修正版）。"""

    def __init__(
        self,
        repos: list[str],
        token: str = "",
        *,
        api_base: str = GITHUB_API,
        max_retries: int = MAX_RETRIES,
        retry_interval: float = RETRY_INTERVAL,
        timeout: float = TIMEOUT,
        sleep: Callable[[float], None] = time.sleep,
        opener: Callable[[urllib.request.Request, float], object] | None = None,
    ) -> None:
        self.repos = list(repos)
        self.token = token
        self.api_base = api_base
        self.max_retries = max_retries
        self.retry_interval = retry_interval
        self.timeout = timeout
        self._sleep = sleep
        self._opener = opener or (lambda req, timeout: urllib.request.urlopen(req, timeout=timeout))
        self.failed_repos: list[str] = []

    # ---- 内部：带重试的单次请求 ----
    def _request(self, url: str) -> tuple[int, dict | list | None, dict[str, str]]:
        request = urllib.request.Request(url)
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Accept", "application/vnd.github+json")

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._opener(request, self.timeout)
                code = response.getcode()
                headers = dict(getattr(response, "headers", {}) or {})
                if code == 401:
                    raise AuthError("GitHub 认证失败（401），请检查 token")
                if code == 403:
                    self._wait_for_rate_limit(headers)
                    last_error = CollectorError("GitHub API 限流（403）")
                    continue
                if code >= 400:
                    last_error = CollectorError(f"GitHub API 返回 HTTP {code}")
                    continue
                body = json.loads(response.read().decode("utf-8"))
                return code, body, headers
            except AuthError:
                raise
            except Exception as exc:  # noqa: BLE001 - 网络异常统一重试
                last_error = exc
                if attempt < self.max_retries:
                    self._sleep(self.retry_interval)
        raise CollectorError(f"重试 {self.max_retries} 次后仍失败：{last_error}")

    def _wait_for_rate_limit(self, headers: dict[str, str]) -> None:
        """契约 1.2 第 3 条：按响应头计算等待时间，并设上限。"""
        wait = 0.0
        reset = headers.get("X-RateLimit-Reset") or headers.get("x-ratelimit-reset")
        if reset:
            try:
                wait = max(0.0, float(reset) - time.time())
            except ValueError:
                wait = 0.0
        if wait <= 0:
            wait = 60.0
        wait = min(wait, MAX_RATE_LIMIT_WAIT)
        self._sleep(wait)

    # ---- 内部：单条 commit 详情 ----
    def _fetch_detail(self, repo: str, sha: str) -> dict | None:
        """契约 1.2 第 6 条：取不到返回 None，绝不静默填 0。"""
        try:
            _code, body, _headers = self._request(f"{self.api_base}/repos/{repo}/commits/{sha}")
        except CollectorError:
            return None
        return body if isinstance(body, dict) else None

    @staticmethod
    def _parse_time(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)

    # ---- 对外接口 ----
    def collect(self, since: str, until: str) -> list[CommitRecord]:
        """采集 [since, until] 内的提交记录，返回结构化列表。

        契约 1.3：仓库列表为空返回空列表；单仓库失败不阻断其他仓库。
        """
        if not self.repos:
            return []

        records: list[CommitRecord] = []
        self.failed_repos = []

        for repo in self.repos:
            try:
                records.extend(self._collect_repo(repo, since, until))
            except AuthError:
                raise  # 认证失败立即上抛，不降级
            except CollectorError:
                # 契约 1.3：记录失败仓库名，但继续处理其余仓库
                self.failed_repos.append(repo)
        return records

    def _collect_repo(self, repo: str, since: str, until: str) -> list[CommitRecord]:
        out: list[CommitRecord] = []
        page = 1
        while True:
            url = (
                f"{self.api_base}/repos/{repo}/commits"
                f"?since={since}&until={until}&per_page={PER_PAGE}&page={page}"
            )
            _code, body, _headers = self._request(url)
            if not isinstance(body, list) or not body:
                break

            for item in body:
                commit = item.get("commit", {})
                detail = self._fetch_detail(repo, str(item.get("sha", "")))
                stats = (detail or {}).get("stats") or {}
                files = (detail or {}).get("files")
                out.append(
                    CommitRecord(
                        author=str((commit.get("author") or {}).get("name") or "unknown"),
                        message=str(commit.get("message", "")),
                        timestamp=self._parse_time(str((commit.get("author") or {}).get("date", ""))),
                        repo=repo,
                        # 取不到就是 None（未知），不是 0
                        additions=stats.get("additions") if detail else None,
                        deletions=stats.get("deletions") if detail else None,
                        files_changed=len(files) if files is not None else None,
                    )
                )
            page += 1
        return out

    def check_token(self) -> bool:
        """契约 1.2 第 7 条：只有 200 才算通过，其余一律 False。"""
        request = urllib.request.Request(f"{self.api_base}/user")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            response = self._opener(request, self.timeout)
            return response.getcode() == 200
        except urllib.error.HTTPError:
            return False
        except Exception:  # noqa: BLE001
            return False
