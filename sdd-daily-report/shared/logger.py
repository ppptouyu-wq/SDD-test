"""统一日志格式：JSON lines。

契约来源：specs/tasks.md Task 2 验收标准："logger.py 输出 JSON lines 格式日志"
配置来源：specs/design.md §6.3 可运维性设计
    "格式：采用JSON lines（便于日志分析工具解析）
     级别：分为INFO（正常流程）+ERROR（异常）
     每次执行记录：开始时间、结束时间、各数据源采集条数及推送结果"
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime",
}


class JsonLinesFormatter(logging.Formatter):
    """把日志记录渲染成一行 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # 允许调用方通过 extra={"source": ...} 附加结构化字段
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def get_logger(name: str = "daily_report", *, level: int = logging.INFO) -> logging.Logger:
    """取得一个输出 JSON lines 的 logger（重复调用不会重复添加 handler）。"""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonLinesFormatter())
        logger.addHandler(handler)
    logger.propagate = False
    return logger
