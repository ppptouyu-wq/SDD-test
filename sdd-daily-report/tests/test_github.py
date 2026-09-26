"""Task 3 验收标准：GitHub 采集模块单元测试。

- collect() 函数签名符合 design.md §4.1 的接口定义
- 返回的每个 CommitRecord 包含全部7个字段且类型正确
- 支持分页查询（GitHub API 默认每页30条）
- API 超时重试3次（间隔5s），若仍失败则标记数据获取失败
- GitHub API 限流（HTTP 403）时，等待 reset 时间后重试
- 使用 Mock 数据的单元测试全部通过
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from collector import github
from shared.models import CollectResult, CommitRecord

SINCE = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)
UNTIL = datetime(2026, 8, 20, 23, 59, tzinfo=timezone.utc)

COMMIT_ITEM = {
    "sha": "abc123def456",
    "commit": {
        "message": "feat: 日报三段式编排",
        "author": {"name": "zhangsan", "date": "2026-08-20T10:12:00Z"},
    },
    "author": {"login": "zhangsan"},
}

# 单条 commit 详情接口才返回 stats 与 files；列表接口不返回。
DETAIL_ITEM = {
    "stats": {"additions": 120, "deletions": 18},
    "files": [{"filename": "a.py"}, {"filename": "b.py"}, {"filename": "c.py"}],
}


def list_or_detail(item: dict = COMMIT_ITEM, detail: dict = DETAIL_ITEM):
    """构造 handler：按 URL 区分"提交列表"与"单条 commit 详情"。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/commits/" in request.url.path:
            return httpx.Response(200, json=detail)
        return httpx.Response(200, json=[item])

    return handler


def test_collect_returns_collect_result_with_seven_fields(config, no_sleep):
    result = github.collect(
        config.github.repos, SINCE, UNTIL,
        config=config.github, client=_client(list_or_detail()), sleep=no_sleep,
    )

    assert isinstance(result, CollectResult)
    assert result.success is True
    assert result.error_message is None
    assert len(result.records) == 1

    record = result.records[0]
    assert isinstance(record, CommitRecord)
    # 全部 7 个字段且类型正确
    assert isinstance(record.author, str) and record.author == "zhangsan"
    assert isinstance(record.message, str) and "日报" in record.message
    assert isinstance(record.timestamp, datetime)
    assert isinstance(record.repo, str) and record.repo == "acme/daily-report"
    assert isinstance(record.additions, int) and record.additions == 120
    assert isinstance(record.deletions, int) and record.deletions == 18
    assert isinstance(record.files_changed, int) and record.files_changed == 3


def test_collect_filters_by_time_range(config, no_sleep):
    """验收标准：能够准确获取指定仓库过去 24h 内的记录 —— since/until 必须上送。"""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=[])

    github.collect(
        config.github.repos, SINCE, UNTIL,
        config=config.github, client=_client(handler), sleep=no_sleep,
    )

    assert seen["since"].startswith("2026-08-20T00:00:00")
    assert seen["until"].startswith("2026-08-20T23:59:00")


def test_collect_supports_pagination(config, no_sleep):
    """验收标准：支持分页查询（每页 30 条）。"""
    list_requests = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/commits/" in request.url.path:  # 单条详情回查
            return httpx.Response(200, json=DETAIL_ITEM)
        list_requests["n"] += 1
        assert request.url.params["per_page"] == "30"
        if list_requests["n"] == 1:
            return httpx.Response(
                200,
                json=[COMMIT_ITEM],
                headers={
                    "Link": '<https://api.github.com/repos/acme/daily-report/commits?page=2>; rel="next"'
                },
            )
        return httpx.Response(200, json=[COMMIT_ITEM, COMMIT_ITEM])

    result = github.collect(
        config.github.repos, SINCE, UNTIL,
        config=config.github, client=_client(handler), sleep=no_sleep,
    )

    assert list_requests["n"] == 2
    assert len(result.records) == 3


def test_commit_detail_is_fetched_for_stats_and_files(config, no_sleep):
    """列表接口不含 stats/files 时必须回查详情，否则统计字段恒为 0。"""
    detail_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "/commits/" in request.url.path:
            detail_calls.append(request.url.path)
            return httpx.Response(200, json=DETAIL_ITEM)
        return httpx.Response(200, json=[COMMIT_ITEM])

    result = github.collect(
        config.github.repos, SINCE, UNTIL,
        config=config.github, client=_client(handler), sleep=no_sleep,
    )

    assert detail_calls == ["/repos/acme/daily-report/commits/abc123def456"]
    record = result.records[0]
    assert record.additions == 120
    assert record.deletions == 18
    assert record.files_changed == 3


def test_detail_fetch_failure_degrades_to_zero_without_dropping_record(config, no_sleep):
    """详情回查失败时应降级为 0，但不得丢掉整条提交记录（优雅降级）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/commits/" in request.url.path:
            return httpx.Response(500, json={"message": "boom"})
        return httpx.Response(200, json=[COMMIT_ITEM])

    result = github.collect(
        config.github.repos, SINCE, UNTIL,
        config=config.github, client=_client(handler), sleep=no_sleep,
    )

    assert result.success is True
    assert len(result.records) == 1
    assert result.records[0].additions == 0
    assert result.records[0].files_changed == 0
    assert result.records[0].message == "feat: 日报三段式编排"


def test_collect_retries_three_times_then_marks_failure(config, no_sleep):
    """验收标准：API 超时重试3次（间隔5s），若仍失败则标记数据获取失败。"""
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        raise httpx.ConnectTimeout("连接超时")

    result = github.collect(
        config.github.repos, SINCE, UNTIL,
        config=config.github, client=_client(handler), sleep=no_sleep,
    )

    assert attempts["n"] == 4  # 首次 + 3 次重试
    assert result.success is False
    assert result.records == []
    assert "重试 3 次后仍失败" in result.error_message


def test_collect_retry_interval_is_five_seconds(config, no_sleep):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("超时")

    github.collect(
        config.github.repos, SINCE, UNTIL,
        config=config.github, client=_client(handler), sleep=no_sleep,
    )

    assert no_sleep.calls == [5.0, 5.0, 5.0]


def test_collect_recovers_on_retry(config, no_sleep):
    """重试后成功：仍应返回成功结果。"""
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/commits/" in request.url.path:
            return httpx.Response(200, json=DETAIL_ITEM)
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ReadTimeout("第一次超时")
        return httpx.Response(200, json=[COMMIT_ITEM])

    result = github.collect(
        config.github.repos, SINCE, UNTIL,
        config=config.github, client=_client(handler), sleep=no_sleep,
    )

    assert result.success is True
    assert len(result.records) == 1


def test_rate_limit_waits_for_reset_then_retries(config, no_sleep):
    """验收标准：GitHub API 限流（HTTP 403）时，等待 reset 时间后重试。"""
    attempts = {"n": 0}
    reset_at = int(datetime.now(tz=timezone.utc).timestamp()) + 42

    def handler(request: httpx.Request) -> httpx.Response:
        if "/commits/" in request.url.path:
            return httpx.Response(200, json=DETAIL_ITEM)
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(
                403,
                json={"message": "API rate limit exceeded"},
                headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset_at)},
            )
        return httpx.Response(200, json=[COMMIT_ITEM])

    result = github.collect(
        config.github.repos, SINCE, UNTIL,
        config=config.github, client=_client(handler), sleep=no_sleep,
    )

    assert attempts["n"] == 2
    assert result.success is True
    assert len(result.records) == 1
    # 等待时间约等于 reset 剩余秒数
    assert no_sleep.calls and 40 <= no_sleep.calls[0] <= 43


def test_one_repo_failure_does_not_block_others(config, no_sleep):
    """design.md §6.1：采集层的失败不应阻塞其他数据源（优雅降级）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        if "broken/repo" in str(request.url):
            return httpx.Response(500, json={"message": "boom"})
        if "/commits/" in request.url.path:
            return httpx.Response(200, json=DETAIL_ITEM)
        return httpx.Response(200, json=[COMMIT_ITEM])

    result = github.collect(
        ["broken/repo", "acme/daily-report"], SINCE, UNTIL,
        config=config.github, client=_client(handler), sleep=no_sleep,
    )

    assert result.success is True
    assert len(result.records) == 1


def test_all_repos_failed_marks_source_failure(config, no_sleep):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "boom"})

    result = github.collect(
        ["broken/repo"], SINCE, UNTIL,
        config=config.github, client=_client(handler), sleep=no_sleep,
    )

    assert result.success is False
    assert result.error_message


def test_empty_repo_list_returns_empty_success(config, no_sleep):
    result = github.collect([], SINCE, UNTIL, config=config.github, sleep=no_sleep)

    assert result.success is True
    assert result.records == []


def _client(handler) -> httpx.Client:
    return httpx.Client(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(handler),
    )
