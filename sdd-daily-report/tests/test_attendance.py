"""Task 11 验收标准：考勤采集模块单元测试（v1.1 新增）。

- collect() 函数签名严格符合 design.md 的接口定义
- 返回 CollectResult 对象（包含 success 标志位）
- 逻辑正确计算工时（check_out - check_in）
- 妥善处理缺失签退数据的情况（状态标注为 "签退缺失"）
- 当 API 不可用时，success 置为 False 并附带详细错误信息
- 基于 Mock 数据的单元测试全部通过
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

import httpx

from collector import lark_attendance
from collector.lark_auth import LarkTokenManager
from shared.models import AttendanceRecord, CollectResult

DAY = date(2026, 8, 20)
TZ = timezone(timedelta(hours=8))
TOKEN_PAYLOAD = {"code": 0, "tenant_access_token": "t-abc", "expire": 7200}


def _client(handler) -> httpx.Client:
    return httpx.Client(
        base_url="https://open.feishu.cn/open-apis",
        transport=httpx.MockTransport(handler),
    )


def _collect(config, handler):
    client = _client(handler)
    return lark_attendance.collect(
        DAY, DAY,
        config=config.lark,
        client=client,
        token_manager=LarkTokenManager(client, config.lark.app_id, config.lark.app_secret),
    )


def _stamp(value: str | None) -> str:
    """把 ISO 时刻转成**纪元秒的数字字符串** —— 真实响应里 check_time 就长这样。"""
    if value is None:
        return ""
    return str(int(datetime.fromisoformat(value).timestamp()))


def _task(
    user: str,
    check_in,
    check_out,
    check_in_result: str = "Normal",
    check_out_result: str = "Normal",
) -> dict:
    """按**真实响应结构**造一条考勤记录（design.md §3.0.1）。

    易错点：签到缺失时 `check_in_result` 为 `Lack` 且**没有 `check_in_record` 键**；
    旧夹具（`user_tasks` + `check_in_time`/`check_date`/`status`）是猜想的结构，
    所以它锁不住"解析读错键名"这个缺陷。
    """
    shift: dict = {
        "check_in_result": check_in_result if check_in else "Lack",
        "check_out_result": check_out_result if check_out else "Lack",
        "check_in_shift_time": _stamp("2026-08-20T09:00:00+08:00"),
        "check_out_shift_time": _stamp("2026-08-20T18:00:00+08:00"),
    }
    if check_in:
        shift["check_in_record"] = {"check_time": _stamp(check_in), "user_id": user}
    if check_out:
        shift["check_out_record"] = {"check_time": _stamp(check_out), "user_id": user}
    return {"user_id": user, "day": 20260820, "records": [shift]}


def test_collect_returns_collect_result_with_success_flag(config):
    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "user_task_results": [
                        _task(
                            "zhangsan@company.com",
                            "2026-08-20T09:32:00+08:00",
                            "2026-08-20T19:05:00+08:00",
                        )
                    ]
                },
            },
        )

    result = _collect(config, handler)

    assert isinstance(result, CollectResult)
    assert result.success is True
    assert result.error_message is None
    assert len(result.records) == 1

    record = result.records[0]
    assert isinstance(record, AttendanceRecord)
    assert record.employee_id == "zhangsan@company.com"
    assert record.date == DAY
    assert record.status == "正常"


def test_work_hours_computed_from_check_in_and_check_out(config):
    """工时（小时）= check_out - check_in。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "user_task_results": [
                        # 09:32 -> 19:05 = 9h33m = 9.55h -> 9.6（四舍五入，保留一位小数）
                        _task(
                            "zhangsan@company.com",
                            "2026-08-20T09:32:00+08:00",
                            "2026-08-20T19:05:00+08:00",
                        ),
                        # 10:00 -> 18:30 = 8.5h
                        _task(
                            "lisi@company.com",
                            "2026-08-20T10:00:00+08:00",
                            "2026-08-20T18:30:00+08:00",
                        ),
                    ]
                },
            },
        )

    result = _collect(config, handler)
    hours = {r.employee_id: r.work_hours for r in result.records}

    assert hours["zhangsan@company.com"] == 9.6
    assert hours["lisi@company.com"] == 8.5


def test_compute_work_hours_helper():
    start = datetime(2026, 8, 20, 9, 0, tzinfo=TZ)
    end = datetime(2026, 8, 20, 18, 30, tzinfo=TZ)

    assert lark_attendance.compute_work_hours(start, end) == 9.5
    assert lark_attendance.compute_work_hours(None, end) == 0.0
    assert lark_attendance.compute_work_hours(start, None) == 0.0


def test_missing_check_out_is_marked_as_missing(config):
    """验收标准：妥善处理缺失签退数据的情况（状态标注为 "签退缺失"）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "user_task_results": [
                        _task("zhangsan@company.com", "2026-08-20T09:32:00+08:00", None)
                    ]
                },
            },
        )

    result = _collect(config, handler)

    record = result.records[0]
    assert record.status == "签退缺失"
    assert record.check_out is None
    assert record.work_hours == 0.0


def test_resolve_status_helper():
    start = datetime(2026, 8, 20, 9, 0, tzinfo=TZ)

    assert lark_attendance.resolve_status("正常", start, start) == "正常"
    assert lark_attendance.resolve_status("正常", start, None) == "签退缺失"
    assert lark_attendance.resolve_status(None, start, start) == "正常"
    # v1.8.4：缺签到但有签退 → 签到缺失；两者都缺 → 缺勤
    assert lark_attendance.resolve_status(None, None, start) == "签到缺失"
    assert lark_attendance.resolve_status(None, None, None) == "缺勤"
    assert lark_attendance.resolve_status("迟到", start, start) == "迟到"


def test_api_unavailable_returns_failure_with_detail(config):
    """验收标准：当 API 不可用时，success 置为 False 并附带详细错误信息。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        return httpx.Response(200, json={"code": 99991672, "msg": "attendance scope missing"})

    result = _collect(config, handler)

    assert result.success is False
    assert result.records == []
    assert "attendance scope missing" in result.error_message


def test_network_failure_returns_failure(config):
    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        raise httpx.ConnectTimeout("连接飞书超时")

    result = _collect(config, handler)

    assert result.success is False
    assert result.error_message


def test_missing_check_in_and_check_out_falls_back_to_check_date(config):
    """API 未返回 `day` 时应回退到查询区间起始日。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        item = _task("wangwu@company.com", "2026-08-20T08:31:00+08:00", "2026-08-20T17:31:00+08:00")
        del item["day"]  # 真实响应里 day 是整数 YYYYMMDD；这里模拟"没给日期"
        return httpx.Response(200, json={"code": 0, "data": {"user_task_results": [item]}})

    result = _collect(config, handler)

    assert result.records[0].date == DAY
    assert result.records[0].work_hours == 9.0


# ==================================== 请求格式契约（依据官方文档实测校准）
# 下面三条锁定请求格式。这三个细节最初全都写错了，导致恒返回
# 99992402 field validation failed；正确格式下会返回业务级错误
# （如 1220001 employeeNos is empty）—— 错误码的变化就是格式正确的判据。


def test_employee_type_is_sent_as_query_param_not_body(config):
    """契约 1：employee_type 必须走 query string，不能放在请求体里。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        seen["params"] = dict(request.url.params)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 0, "data": {"user_task_results": []}})

    client = _client(handler)
    lark_attendance.collect(
        DAY, DAY, config=config.lark, employee_type="employee_no",
        client=client,
        token_manager=LarkTokenManager(client, config.lark.app_id, config.lark.app_secret),
    )

    assert seen["params"]["employee_type"] == "employee_no"
    assert "employee_type" not in seen["body"]


def test_dates_are_integers_in_yyyymmdd_format(config):
    """契约 2：日期必须是整数 YYYYMMDD（20260820），不是 ISO 字符串。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 0, "data": {"user_task_results": []}})

    client = _client(handler)
    lark_attendance.collect(
        DAY, DAY, config=config.lark,
        client=client,
        token_manager=LarkTokenManager(client, config.lark.app_id, config.lark.app_secret),
    )

    assert seen["body"]["check_date_from"] == 20260820
    assert seen["body"]["check_date_to"] == 20260820
    assert isinstance(seen["body"]["check_date_from"], int)


def test_user_ids_is_sent_in_body(config):
    """契约 3：user_ids 必须走请求体，且是数组。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 0, "data": {"user_task_results": []}})

    client = _client(handler)
    lark_attendance.collect(
        DAY, DAY, config=config.lark, user_ids=["emp_001"],
        client=client,
        token_manager=LarkTokenManager(client, config.lark.app_id, config.lark.app_secret),
    )

    assert seen["body"]["user_ids"] == ["emp_001"]


def test_business_error_code_is_surfaced_not_swallowed(config):
    """飞书在 HTTP 400 时也返回结构化 code/msg —— 必须透传，不能只说 HTTP 400。

    实测教训：早期实现只抛 "HTTP 400"，把 `employeeNos is empty`
    这类可定位问题的业务提示丢掉了。
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        return httpx.Response(400, json={"code": 1220001, "msg": "employeeNos is empty"})

    result = _collect(config, handler)

    assert result.success is False
    assert "employeeNos is empty" in result.error_message


# ==================================== v1.8.4：解析真实响应结构（缺陷修复的回归网）
# 缺陷本体：代码读的是猜想的 `data.user_tasks` 与 `check_in_time`/`check_out_time`/
# `check_date`/`status`，这些键在真实响应里都不存在 → 真实打卡被静默丢成
# `success=True, count=0`，日报一路渲染成「今日无记录」。
# 下面这组夹具用**实测响应**（2026-10-13）的形状，锁住这个缺陷。

REAL_DAY = date(2026, 10, 13)
REAL_RESPONSE = {
    "code": 0,
    "data": {
        "user_task_results": [
            {
                "user_id": "emp_0001",
                "day": 20261013,
                "employee_name": "示例员工",
                "group_id": "7000000000000000001",
                "shift_id": "8",
                "result_id": "7000000000000000002",
                "records": [
                    {
                        "check_in_result": "Lack",
                        "check_out_result": "Normal",
                        "check_in_result_supplement": "None",
                        "check_out_result_supplement": "None",
                        "check_in_shift_time": "1791853200",
                        "check_out_shift_time": "1791885600",
                        "check_in_record_id": "",
                        "check_out_record_id": "7000000000000000003",
                        "task_shift_type": 0,
                        "check_out_record": {
                            "check_time": "1791894294",
                            "user_id": "emp_0001",
                            "creator_id": "emp_0001",
                            "record_id": "7000000000000000003",
                            "device_id": "0000000000000000000000000000000000000000000000000000000000000000",
                            "type": 0,
                            "is_field": False,
                            "is_wifi": False,
                            "latitude": 0,
                            "longitude": 0,
                            "comment": "",
                            "ssid": "",
                            "bssid": "",
                            "external_id": "",
                            "photo_urls": [],
                        },
                    }
                ],
            }
        ]
    },
}


def test_real_response_shape_is_parsed(config):
    """实测响应（签到 Lack + 签退 20:24:54）必须被解析出来，而不是 0 条。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        return httpx.Response(200, json=REAL_RESPONSE)

    result = _collect(config, handler)

    assert result.success is True
    assert len(result.records) == 1

    record = result.records[0]
    assert record.employee_id == "emp_0001"
    assert record.date == REAL_DAY
    assert record.check_in is None
    assert record.check_out == datetime(2026, 10, 13, 20, 24, 54, tzinfo=TZ)
    assert record.work_hours == 0.0
    assert record.status == "签到缺失"  # v1.8.4 新增状态


def test_multiple_shifts_take_earliest_check_in_and_latest_check_out(config):
    """一天多段班次 → 取最早签到 + 最晚签退。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        item = _task(
            "zhangsan@company.com",
            "2026-08-20T09:30:00+08:00",
            "2026-08-20T12:00:00+08:00",
        )
        second = _task(
            "zhangsan@company.com",
            "2026-08-20T14:00:00+08:00",
            "2026-08-20T19:00:00+08:00",
        )
        item["records"] = item["records"] + second["records"]
        return httpx.Response(200, json={"code": 0, "data": {"user_task_results": [item]}})

    result = _collect(config, handler)

    record = result.records[0]
    assert record.check_in == datetime(2026, 8, 20, 9, 30, tzinfo=TZ)
    assert record.check_out == datetime(2026, 8, 20, 19, 0, tzinfo=TZ)
    assert record.work_hours == 9.5


def test_day_without_any_punch_is_marked_absent(config):
    """既无签到也无签退 → 缺勤（不是默默丢成 0 条）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        item = {
            "user_id": "zhangsan@company.com",
            "day": 20260820,
            "records": [{"check_in_result": "Lack", "check_out_result": "Lack"}],
        }
        return httpx.Response(200, json={"code": 0, "data": {"user_task_results": [item]}})

    result = _collect(config, handler)

    assert len(result.records) == 1
    assert result.records[0].status == "缺勤"
    assert result.records[0].work_hours == 0.0


class _CapturedLogs(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_unexpected_response_shape_logs_a_warning_instead_of_silent_zero(config):
    """响应结构变了必须出声 —— 静默返回 0 条正是这次缺陷藏了三个月的原因。"""
    handler_logs = _CapturedLogs()
    target = logging.getLogger("collector.lark_attendance")
    target.addHandler(handler_logs)
    target.setLevel(logging.WARNING)
    try:

        def handler(request: httpx.Request) -> httpx.Response:
            if "tenant_access_token" in str(request.url):
                return httpx.Response(200, json=TOKEN_PAYLOAD)
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})

        result = _collect(config, handler)
    finally:
        target.removeHandler(handler_logs)

    assert result.success is True
    assert result.records == []
    warnings = [r for r in handler_logs.records if r.levelno == logging.WARNING]
    assert warnings, "响应里没有 user_task_results 时必须打 WARNING"
    assert "response_keys" in getattr(warnings[0], "__dict__", {})


def test_epoch_second_strings_are_parsed():
    """check_time 是纪元秒的数字字符串；毫秒也能认（消息接口是毫秒）。"""
    expected = datetime(2026, 10, 13, 20, 24, 54, tzinfo=TZ)

    assert lark_attendance._parse_datetime("1791894294") == expected
    assert lark_attendance._parse_datetime("1791894294000") == expected
    assert lark_attendance._parse_datetime(1791894294) == expected
    assert lark_attendance._parse_datetime("") is None
    assert lark_attendance._parse_datetime(None) is None
