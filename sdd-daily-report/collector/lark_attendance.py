"""飞书考勤采集模块（Task 11，v1.1 新增）。

规范来源：specs/tasks.md Task 11（由第 7 章 7.5.4 Step 3 新增）
    Task 11: 实现考勤数据采集模块（新增 v1.1）

验收标准（tasks.md Task 11）：
- [x] collect() 函数签名严格符合 design.md 的接口定义
- [x] 返回 CollectResult 对象（包含 success 标志位）
- [x] 逻辑正确计算工时（check_out - check_in）
- [x] 妥善处理缺失签退数据的情况（状态标注为 "签退缺失"）
- [x] 当 API 不可用时，success 置为 False 并附带详细错误信息
- [x] 基于 Mock 数据的单元测试全部通过

接口契约（design.md §4.1 / 7.5.3 Step 2）：
    # collector/lark_attendance.py
    def collect(since: date, until: date) -> CollectResult[AttendanceRecord]

--------------------------------------------------------------------------
✅ 真实链路验证状态：**请求格式已校准通过**（2026-09-26 实测）

初版请求格式有三处与官方文档不符，实测恒返回 `99992402 field validation failed`：

| 参数 | 初版（错） | 官方文档（对） |
|---|---|---|
| `employee_type` | 放在请求体 | **query string** |
| `check_date_from` / `check_date_to` | ISO 字符串 `"2026-04-24"` | **整数 `20260424`** |
| `user_ids` | 放在请求体 | 放在请求体（这一处原本就对） |

校准后实测返回 **业务级错误** `1220001 employeeNos is empty`，
而非格式级错误 `99992402` —— **错误码的变化即格式正确的判据**。
（该租户没有考勤员工数据，所以拿不到真实记录，但请求构造已确认正确。）

本次还修正了一个错误信息透传缺陷：飞书在 HTTP 400 时同样返回结构化
`code`/`msg`，早期实现只抛 `"HTTP 400"`，把 `employeeNos is empty`
这类可定位问题的提示丢掉了。现在会原样带出。

三条格式契约已由 `tests/test_attendance.py` 锁定。
--------------------------------------------------------------------------
"""

from __future__ import annotations

import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Callable

import httpx

from shared.config import LarkConfig
from shared.errors import CollectorError
from shared.logger import get_logger
from shared.models import AttendanceRecord, CollectResult

from collector.lark_auth import LarkTokenManager
from collector.lark_task import TOKEN_INVALID_CODES

logger = get_logger("collector.lark_attendance")

STATUS_MISSING_CHECKOUT = "签退缺失"
TZ = timezone(timedelta(hours=8))


def compute_work_hours(check_in: datetime | None, check_out: datetime | None) -> float:
    """工时 = check_out - check_in（小时，保留一位小数）。"""
    if check_in is None or check_out is None:
        return 0.0
    delta = check_out - check_in
    return round(max(delta.total_seconds(), 0.0) / 3600.0, 1)


def resolve_status(
    raw_status: str | None, check_in: datetime | None, check_out: datetime | None
) -> str:
    """缺失签退数据时状态标注为"签退缺失"，否则使用接口返回的状态。"""
    if check_in is not None and check_out is None:
        return STATUS_MISSING_CHECKOUT
    if raw_status:
        return str(raw_status)
    return "正常"


def _parse_datetime(value: object) -> datetime | None:
    if value in (None, "", 0, "0"):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e11:  # 毫秒
            seconds /= 1000.0
        return datetime.fromtimestamp(seconds, tz=TZ)
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    return parsed


def collect(
    since: date,
    until: date,
    *,
    config: LarkConfig,
    employee_type: str = "employee_no",
    user_ids: list[str] | None = None,
    client: httpx.Client | None = None,
    token_manager: LarkTokenManager | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> CollectResult[AttendanceRecord]:
    """采集 [since, until] 区间的考勤数据。API 不可用时返回 success=False。

    请求格式依据飞书官方文档（2026-09 核对）：
      - `employee_type` 是 **query 参数**（不是 body 字段）
      - `user_ids` / `check_date_from` / `check_date_to` 是 **body 字段**
      - 两个日期是 **整数 YYYYMMDD**（如 20260424），不是 ISO 字符串
    这三点与最初的推断都不同，实测确认后会得到业务级错误
    （如 1220001 employeeNos is empty）而非 99992402 字段校验失败。
    """
    owns_client = client is None
    if client is None:
        client = httpx.Client(base_url=config.api_base, timeout=config.timeout)
    tokens = token_manager or LarkTokenManager(client, config.app_id, config.app_secret)

    try:
        # employee_type 走 query；user_ids 与日期走 body；日期为整数 YYYYMMDD
        query = {"employee_type": employee_type}
        body = {
            "user_ids": list(user_ids or []),
            "check_date_from": int(since.strftime("%Y%m%d")),
            "check_date_to": int(until.strftime("%Y%m%d")),
        }
        data = _request_with_auth_retry(
            client, tokens, "/attendance/v1/user_tasks/query",
            query, body, config, sleep,
        )

        records: list[AttendanceRecord] = []
        items = ((data.get("data") or {}).get("user_tasks")) or []
        for item in items:
            check_in = _parse_datetime(item.get("check_in_time"))
            check_out = _parse_datetime(item.get("check_out_time"))
            records.append(
                AttendanceRecord(
                    employee_id=str(item.get("employee_id") or item.get("user_id") or ""),
                    date=_parse_date(item.get("check_date"), since),
                    check_in=check_in,
                    check_out=check_out,
                    work_hours=compute_work_hours(check_in, check_out),
                    status=resolve_status(item.get("status"), check_in, check_out),
                )
            )

        logger.info(
            "飞书考勤采集完成",
            extra={"source": "lark_attendance", "count": len(records)},
        )
        return CollectResult.ok(records)
    except CollectorError as exc:
        logger.error("飞书考勤采集失败", extra={"source": "lark_attendance", "error": exc.message})
        return CollectResult.fail(exc.message)
    finally:
        if owns_client:
            client.close()


def _parse_date(value: object, fallback: date) -> date:
    if not value:
        return fallback
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return fallback


def _request_with_auth_retry(
    client: httpx.Client,
    tokens: LarkTokenManager,
    path: str,
    query: dict[str, object],
    body: dict[str, object],
    config: LarkConfig,
    sleep: Callable[[float], None],
) -> dict:
    """带"Token 过期刷新后重试 1 次"与"超时重试"的 POST。

    注意 `query` 与 `body` 是分开的：飞书考勤接口的 `employee_type` 必须走
    query string，而 `user_ids`/日期必须走请求体。
    """
    token_retried = False
    last_error: Exception | None = None

    for attempt in range(config.max_retries + 1):
        try:
            response = client.post(
                path,
                params=query,
                json=body,
                headers={"Authorization": f"Bearer {tokens.get_token()}"},
            )
            # 飞书在 HTTP 400 时也会返回结构化的 code/msg（如 1220001 参数错误）。
            # 必须要把它带出来，否则上层只能看到"HTTP 400"，无法定位问题。
            try:
                data = response.json()
            except ValueError:
                data = {}
            if response.status_code >= 400 and not data.get("code"):
                raise CollectorError(
                    f"飞书考勤 API 返回 HTTP {response.status_code}", source="lark_attendance"
                )

            if data.get("code") in TOKEN_INVALID_CODES:
                if not token_retried:
                    token_retried = True
                    logger.error(
                        "飞书 Token 已过期，刷新后重试",
                        extra={"source": "lark_attendance", "code": data.get("code")},
                    )
                    tokens.invalidate()
                    continue
                raise CollectorError(
                    f"飞书 Token 刷新后仍无效：{data.get('msg')}", source="lark_attendance"
                )

            if data.get("code") not in (0, None):
                raise CollectorError(
                    f"飞书考勤 API 错误：{data.get('msg')}", source="lark_attendance"
                )
            return data

        except (httpx.HTTPError, CollectorError) as exc:
            last_error = exc
            if attempt < config.max_retries:
                logger.error(
                    "飞书考勤采集失败，准备重试",
                    extra={"source": "lark_attendance", "attempt": attempt + 1, "error": str(exc)},
                )
                sleep(0.0)

    raise CollectorError(
        f"飞书考勤 API 重试 {config.max_retries} 次后仍失败：{last_error}",
        source="lark_attendance",
    )
