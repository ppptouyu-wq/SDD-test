"""展示层 HTTP 薄壳（v1.2 需求变更新增）。

契约来源：specs/design.md §4（``webview.serve``）、§6.5（只读 / 只监听回环地址 / 零新增依赖）
          specs/contracts/api-spec.yaml 的 paths（三个只读端点）
验收来源：specs/tasks.md Task 12、specs/proposal.md §3.3（v1.2 新增条目）
边界决策：specs/adrs/004-展示层技术选型.md（ADR-004）

本模块只做"HTTP 请求 → views 调用 → HTTP 响应"的翻译，**不含业务判断**：
业务逻辑全在 ``views.py``，因此可以在不启动服务的情况下被测试。
运行时只使用 Python 标准库（``http.server`` / ``json`` / ``urllib.parse``），
不引入任何 Web 框架，也不需要构建步骤（design.md §6.5(3)）。
"""

from __future__ import annotations

import json
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from shared.errors import StorageError
from shared.logger import get_logger
from shared.storage import ReportStorage
from webview import views

logger = get_logger("webview")

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_FILE = "index.html"
STYLESHEET_FILE = "app.css"

#: design.md §6.5(2)：只允许回环地址；出现 0.0.0.0 一律拒绝启动。
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")

#: 兜底库路径。锚在项目根而不是 cwd：合并成单仓库后项目嵌在子目录里，
#: 从仓库根执行 `python sdd-daily-report/main.py --serve` 是自然动作。
#: （main.py::_serve 通常会传入显式路径，这里只服务于直接调用 serve() 的场景。）
DEFAULT_DB_PATH = str(Path(__file__).resolve().parent.parent / "data" / "reports.db")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

_REPORTS_PREFIX = "/api/reports/"


class ReportRequestHandler(BaseHTTPRequestHandler):
    """只读请求处理器。

    写方法（POST/PUT/DELETE/PATCH）一律 405，且不触碰数据库 ——
    这是 ``proposal.md`` §2.2"展示页为只读、无任何写接口"在 HTTP 层的落点，
    并由 ``api-spec.yaml`` 的 ``writeNotSupported`` 端点固定在契约里。
    """

    server_version = "SDDReportView/1.2"
    sys_version = ""

    # ---- 只读：写操作一律拒绝 ----
    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 要求的命名
        self._reject_write()

    def do_PUT(self) -> None:  # noqa: N802
        self._reject_write()

    def do_DELETE(self) -> None:  # noqa: N802
        self._reject_write()

    def do_PATCH(self) -> None:  # noqa: N802
        self._reject_write()

    # ---- 读取 ----
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._send_static(INDEX_FILE, "text/html; charset=utf-8")
        if path in ("/static/app.css", "/app.css"):
            return self._send_static(STYLESHEET_FILE, "text/css; charset=utf-8")
        if path == "/api/reports":
            items = views.list_reports(self.server.storage)  # type: ignore[attr-defined]
            return self._send_json(200, [views.to_jsonable(item) for item in items])
        if path.startswith(_REPORTS_PREFIX):
            return self._send_report_detail(unquote(path[len(_REPORTS_PREFIX):]))
        return self._send_error_json(404, "未找到该地址", extra={"path": path})

    def _send_report_detail(self, raw_date: str) -> None:
        """``GET /api/reports/{date}``。

        404 时返回明确错误体（页面显示"该日期没有日报记录"），而不是空白页；
        400 用于日期格式非法（``proposal.md`` §3.3 v1.2）。
        """
        try:
            day = date.fromisoformat(raw_date)
        except ValueError:
            return self._send_error_json(
                400, f"日期格式非法：{raw_date}（应为 YYYY-MM-DD）", extra={"date": raw_date}
            )
        detail = views.get_report(self.server.storage, day)  # type: ignore[attr-defined]
        if detail is None:
            return self._send_error_json(
                404, views.TEXT_NO_REPORT_FOR_DATE, extra={"date": day.isoformat()}
            )
        return self._send_json(200, views.to_jsonable(detail))

    # ---- 响应工具 ----
    def _reject_write(self) -> None:
        self._send_error_json(
            405,
            "展示层为只读，不支持任何写操作",
            extra={"method": self.command or "", "path": urlparse(self.path).path},
        )

    def _send_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, message: str, *, extra: dict | None = None) -> None:
        payload: dict = {"error": message, "status": status}
        if extra:
            payload.update(extra)
        self._send_json(status, payload)

    def _send_static(self, filename: str, content_type: str) -> None:
        target = (STATIC_DIR / filename).resolve()
        # 静态文件白名单：只允许 STATIC_DIR 下的文件，避免路径穿越。
        if STATIC_DIR not in target.parents or not target.is_file():
            return self._send_error_json(404, "未找到该静态文件", extra={"file": filename})
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """把访问日志并入统一的 JSON lines 日志（design.md §6.3）。"""
        logger.info(
            "展示页访问",
            extra={"source": "webview", "client": self.address_string(), "line": format % args},
        )


class ViewServer(HTTPServer):
    """单线程 HTTP 服务。

    ADR-004 已记录：单机单用户的只读展示页用 ``HTTPServer`` 足够，
    引入多线程/ASGI 属于用复杂度换用不上的能力。

    **不要改成 ``ThreadingHTTPServer``**：``ReportStorage`` 持有的 SQLite 连接有
    线程亲和性（"SQLite objects created in a thread can only be used in that same
    thread"），当前实现依赖"建连接与处理请求在同一个线程"这一前提 ——
    ``serve()`` 正是这样做的。要改多线程，必须先把连接改成每线程一个。
    """

    def __init__(self, address: tuple[str, int], storage: ReportStorage) -> None:
        super().__init__(address, ReportRequestHandler)
        self.storage = storage


def serve(
    db_path: str = DEFAULT_DB_PATH,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> int:
    """在本机启动只读展示页（阻塞直到 Ctrl+C）。

    返回进程退出码：0 = 正常停止，2 = 启动前校验未通过。
    """
    if host not in LOOPBACK_HOSTS:
        print(
            "拒绝启动：展示层只允许绑定回环地址"
            f"（{'、'.join(LOOPBACK_HOSTS)}），收到 host={host!r}"
        )
        return 2

    try:
        # readonly=True：SQLite 以 mode=ro 打开，写操作在连接层面即失败。
        storage = ReportStorage(db_path, readonly=True)
    except StorageError as exc:
        print(f"无法打开日报库：{exc}")
        return 2

    try:
        httpd = ViewServer((host, port), storage)
    except OSError as exc:
        print(f"无法监听 {host}:{port}：{exc}")
        storage.close()
        return 2

    print(f"日报展示页已启动：http://{host}:{port}/  （只读，按 Ctrl+C 停止）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n展示页已停止")
    finally:
        httpd.server_close()
        storage.close()
    return 0
