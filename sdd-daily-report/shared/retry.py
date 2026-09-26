"""重试策略工具。

配置来源：specs/design.md §6.1 错误处理策略
    "GitHub API超时 | 重试 3 次（间隔 5s），若仍失败则标记'数据获取失败'"
    "邮件发送失败 | 重试 2 次，若仍失败则记录日志和飞书消息告警"
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

T = TypeVar("T")


def retry_call(
    func: Callable[[], T],
    *,
    max_retries: int = 3,
    interval: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """执行 func，失败时重试。

    :param max_retries: 重试次数（不含首次执行）。0 表示只执行一次。
    :param interval: 每次重试前的等待秒数。
    :param sleep: 便于测试注入（默认 time.sleep）。
    :raises: 最后一次尝试抛出的异常。
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 - 采集层需要兜住所有网络异常
            last_exc = exc
            if attempt < max_retries:
                if interval:
                    sleep(interval)
    assert last_exc is not None
    raise last_exc
