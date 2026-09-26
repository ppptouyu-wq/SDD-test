"""把 data/reports.db 里的日报导出成文件，方便直接用编辑器/浏览器查看。

用法（在 sdd-daily-report/ 目录下）：
    python _show_report.py                 # 导出全部记录
    python _show_report.py 2026-08-20      # 只导出指定日期

导出位置：data/reports/<日期>.md 与 data/reports/<日期>.html
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB = Path("data/reports.db")
OUT = Path("data/reports")

if not DB.is_file():
    raise SystemExit(f"找不到 {DB}，请先跑一次：python main.py --config config.yaml --date 2026-08-20 --mock")

conn = sqlite3.connect(str(DB))
wanted = sys.argv[1] if len(sys.argv) > 1 else None

sql = "SELECT report_date, team_name, generated_at, markdown, html FROM daily_reports"
args: tuple = ()
if wanted:
    sql += " WHERE report_date = ?"
    args = (wanted,)
sql += " ORDER BY report_date"

rows = conn.execute(sql, args).fetchall()
if not rows:
    raise SystemExit(f"数据库里没有 {wanted or '任何'} 记录")

OUT.mkdir(parents=True, exist_ok=True)
for report_date, team_name, generated_at, markdown, html in rows:
    md_path = OUT / f"{report_date}.md"
    html_path = OUT / f"{report_date}.html"
    md_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(html, encoding="utf-8")
    print(f"{report_date}  {team_name}  生成于 {generated_at[:19]}")
    print(f"    {md_path}    ({len(markdown)} 字符)")
    print(f"    {html_path}  ({len(html)} 字符)")

print(f"\n共导出 {len(rows)} 条到 {OUT.resolve()}")
