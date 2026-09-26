"""SQLite 存储（仅用于日报历史记录）。

契约来源：specs/tasks.md Task 2 验收标准
    "storage.py 能创建 SQLite，写入和查询日报记录"
决策来源：specs/adrs/002-数据存储选型.md（ADR-002）
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from shared.errors import StorageError
from shared.models import DailyReport

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_reports (
    report_date  TEXT PRIMARY KEY,
    team_name    TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    markdown     TEXT NOT NULL,
    html         TEXT NOT NULL,
    payload      TEXT NOT NULL
)
"""


class ReportStorage:
    """日报历史存储。

    对应 ADR-002：零部署、零运维；每天仅执行一次，不存在并发写入场景。
    """

    def __init__(self, db_path: str | Path = "data/reports.db") -> None:
        self.db_path = Path(db_path)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.execute(_SCHEMA)
            self._conn.commit()
        except sqlite3.Error as exc:
            raise StorageError(f"无法初始化 SQLite（{self.db_path}）：{exc}") from exc

    # ---- 写入 ----
    def save(self, report: DailyReport) -> None:
        payload = json.dumps(_to_jsonable(report), ensure_ascii=False)
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO daily_reports "
                "(report_date, team_name, generated_at, markdown, html, payload) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    report.date.isoformat(),
                    report.team_name,
                    report.generated_at.isoformat(),
                    report.markdown,
                    report.html,
                    payload,
                ),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            raise StorageError(f"写入日报失败：{exc}") from exc

    # ---- 查询 ----
    def get(self, report_date: date) -> dict | None:
        cursor = self._conn.execute(
            "SELECT report_date, team_name, generated_at, markdown, html, payload "
            "FROM daily_reports WHERE report_date = ?",
            (report_date.isoformat(),),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "date": row[0],
            "team_name": row[1],
            "generated_at": row[2],
            "markdown": row[3],
            "html": row[4],
            "payload": json.loads(row[5]),
        }

    def count(self) -> int:
        cursor = self._conn.execute("SELECT COUNT(*) FROM daily_reports")
        return int(cursor.fetchone()[0])

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ReportStorage":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _to_jsonable(report: DailyReport) -> dict:
    """把 DailyReport 转成可 JSON 序列化的字典（datetime/date 转 isoformat）。"""

    def convert(value: object) -> object:
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, dict):
            return {k: convert(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [convert(v) for v in value]
        return value

    return convert(asdict(report))  # type: ignore[return-value]
