"""共享测试夹具。

对应 tasks.md 各任务的验收标准："使用 Mock 数据的单元测试全部通过"。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

# 让测试不依赖"从哪个目录运行"：把项目根加入 import 路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared.config import (
    AppConfig,
    EmailConfig,
    GitHubConfig,
    LarkConfig,
    Member,
    ReportConfig,
)

DAY = date(2026, 8, 20)  # 周四，工作日


@pytest.fixture
def tmp_dir():
    """工作区内的临时目录。

    不使用 pytest 内置 tmp_path：其 basetemp 默认落在系统临时目录
    （Windows 下常为 %TEMP%\\pytest-of-<user>），在受限环境下不可写。
    这里用固定名字的目录并在每次使用前清空，保证可写且互不干扰。
    """
    path = Path(__file__).resolve().parent.parent / "test_tmp"
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def members() -> list[Member]:
    return [
        Member(name="张三", github="zhangsan", lark="zhangsan@company.com"),
        Member(name="李四", github="lisi-dev", lark="lisi@company.com"),
        Member(name="王五", github="wangwu", lark="wangwu@company.com"),
    ]


@pytest.fixture
def config(members: list[Member], tmp_dir: Path) -> AppConfig:
    return AppConfig(
        members=members,
        github=GitHubConfig(
            token="ghp_test",
            repos=["acme/daily-report"],
            max_retries=3,
            retry_interval=5.0,
        ),
        lark=LarkConfig(
            app_id="cli_test",
            app_secret="secret_test",
            project_id="proj_demo_001",
            chat_id="oc_demo_chat_001",
            keywords=["项目", "需求", "bug", "发布"],
            max_retries=3,
        ),
        email=EmailConfig(
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_user="bot@example.com",
            smtp_password="pw",
            sender="bot@example.com",
            recipients=["leader@example.com"],
        ),
        report=ReportConfig(
            team_name="平台研发组",
            sensitive_keywords=["薪资", "绩效", "裁员", "期权"],
            holidays=["2026-10-01"],
        ),
        storage_path=str(tmp_dir / "reports.db"),
    )


@pytest.fixture
def no_sleep():
    """替代 time.sleep，记录调用但不真的等待。"""

    calls: list[float] = []

    def _sleep(seconds: float) -> None:
        calls.append(seconds)

    _sleep.calls = calls  # type: ignore[attr-defined]
    return _sleep


def make_client(handler) -> httpx.Client:
    """用 httpx.MockTransport 构造不发起真实请求的 client。"""
    return httpx.Client(
        base_url="https://api.example.com",
        transport=httpx.MockTransport(handler),
    )


def json_response(payload: dict | list, status_code: int = 200, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        headers=headers or {},
        content=json.dumps(payload).encode("utf-8"),
    )


@pytest.fixture
def ts():
    """固定时间戳工厂（UTC）。"""

    def _ts(hour: int, minute: int = 0) -> datetime:
        return datetime(2026, 8, 20, hour, minute, tzinfo=timezone.utc)

    return _ts
