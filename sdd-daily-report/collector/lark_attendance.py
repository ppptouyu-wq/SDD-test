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
（该租户当时没有考勤员工数据，所以拿不到真实记录，但请求构造已确认正确。）

本次还修正了一个错误信息透传缺陷：飞书在 HTTP 400 时同样返回结构化
`code`/`msg`，早期实现只抛 `"HTTP 400"`，把 `employeeNos is empty`
这类可定位问题的提示丢掉了。现在会原样带出。

三条格式契约已由 `tests/test_attendance.py` 锁定。

--------------------------------------------------------------------------
⚠️ v1.8.4 缺陷修复：**响应解析**曾经整段是错的（2026-10-13 实测发现）

请求格式对，但**读取响应的键名是猜的**：代码读 `data.user_tasks` 与
`check_in_time` / `check_out_time` / `check_date` / `status`，而这些键在真实响应里
**都不存在**（真实键是 `data.user_task_results`，时刻在
`records[].check_in_record.check_time`，日期是整数 `day`）。
读不到就"成功地返回 0 条"，日报一路渲染成「今日无记录」——
**看起来像"没打卡"，其实是"没解析到"**。真实响应结构与字段映射见
`specs/design.md` §3.0.1，判定表见同节末尾。
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
STATUS_MISSING_CHECKIN = "签到缺失"
STATUS_ABSENT = "缺勤"
STATUS_NORMAL = "正常"
STATUS_LATE = "迟到"
STATUS_EARLY = "早退"
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
    """按"签到/签退是否齐全"判定状态；齐全时使用接口返回的状态。

    判定顺序见 design.md §3.0.1：
    两者都缺 → 缺勤；缺签到 → 签到缺失；缺签退 → 签退缺失；齐全 → 接口状态或"正常"。
    """
    if check_in is None and check_out is None:
        return STATUS_ABSENT
    if check_in is None:
        return STATUS_MISSING_CHECKIN
    if check_out is None:
        return STATUS_MISSING_CHECKOUT
    if raw_status:
        return str(raw_status)
    return STATUS_NORMAL


def _parse_datetime(value: object) -> datetime | None:
    """解析打卡时刻：纪元秒/毫秒（数字或数字字符串）与 ISO 字符串都要认。

    实测（2026-10-13）`check_in_record.check_time` 是**纪元秒的数字字符串**
    （如 `"1791894294"`），而飞书消息接口的 `create_time` 是**毫秒**，
    所以按量级判断（1e11 秒 ≈ 公元 5138 年，故 ≥1e11 必为毫秒）。
    """
    if value in (None, "", 0, "0"):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _epoch_to_datetime(float(value))
    text = str(value).strip()
    if text.isdigit():
        return _epoch_to_datetime(float(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    return parsed


def _epoch_to_datetime(value: float) -> datetime:
    seconds = value / 1000.0 if value >= 1e11 else value
    return datetime.fromtimestamp(seconds, tz=TZ)


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
        payload = data.get("data") or {}
        items = payload.get("user_task_results")
        if items is None:
            # 解析不到时必须出声：静默返回 0 条会一路渲染成"今日无记录"，
            # 而"0 条"看起来完全合法（v1.8.4 的缺陷就是这样藏了三个月）。
            items = payload.get("user_tasks")
            if items is None:
                logger.warning(
                    "飞书考勤响应缺少 user_task_results 字段，无法解析",
                    extra={
                        "source": "lark_attendance",
                        "response_keys": sorted(payload.keys()),
                    },
                )
        for item in items or []:
            check_in, check_out, raw_status = _merge_shifts(item.get("records"))
            records.append(
                AttendanceRecord(
                    employee_id=str(item.get("user_id") or item.get("employee_id") or ""),
                    date=_parse_date(item.get("day") or item.get("check_date"), since),
                    check_in=check_in,
                    check_out=check_out,
                    work_hours=compute_work_hours(check_in, check_out),
                    status=resolve_status(raw_status, check_in, check_out),
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


def _record_time(record: object) -> datetime | None:
    """从 `check_in_record` / `check_out_record` 里取打卡时刻。"""
    if not isinstance(record, dict):
        return None
    return _parse_datetime(record.get("check_time"))


def _merge_shifts(shifts: object) -> tuple[datetime | None, datetime | None, str]:
    """把一天的多段班次合成「最早签到 + 最晚签退 + 接口状态」。

    `records[]` 里每段班次形如
    `{"check_in_result": "Lack", "check_out_result": "Normal",
      "check_in_record": {...}, "check_out_record": {...}}`；
    **签到缺失时 `check_in_record` 键不存在**（`check_in_result` 为 `Lack`）。

    接口状态只用来区分「迟到 / 早退」，签到签退是否齐全由时刻本身判定
    （见 design.md §3.0.1 的判定表）。
    """
    check_ins: list[datetime] = []
    check_outs: list[datetime] = []
    raw_status = ""
    for shift in shifts or []:
        if not isinstance(shift, dict):
            continue
        moment = _record_time(shift.get("check_in_record"))
        if moment is not None:
            check_ins.append(moment)
        moment = _record_time(shift.get("check_out_record"))
        if moment is not None:
            check_outs.append(moment)
        if not raw_status:
            if str(shift.get("check_in_result") or "") == "Late":
                raw_status = STATUS_LATE
            elif str(shift.get("check_out_result") or "") == "Early":
                raw_status = STATUS_EARLY
    return (
        min(check_ins) if check_ins else None,
        max(check_outs) if check_outs else None,
        raw_status,
    )


def _parse_date(value: object, fallback: date) -> date:
    """解析考勤日期：真实响应给的是整数 `20261013`，也兼容 `"2026-10-13"`。"""
    if not value:
        return fallback
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    try:
        return date.fromisoformat(text[:10])
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
