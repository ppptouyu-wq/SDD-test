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


def _task(user: str, check_in, check_out, status: str | None = None) -> dict:
    item = {
        "employee_id": user,
        "check_date": "2026-08-20",
        "check_in_time": check_in,
        "check_out_time": check_out,
    }
    if status:
        item["status"] = status
    return item


def test_collect_returns_collect_result_with_success_flag(config):
    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "user_tasks": [
                        _task(
                            "zhangsan@company.com",
                            "2026-08-20T09:32:00+08:00",
                            "2026-08-20T19:05:00+08:00",
                            "正常",
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
                    "user_tasks": [
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
                    "user_tasks": [
                        _task("zhangsan@company.com", "2026-08-20T09:32:00+08:00", None, "正常")
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
    """API 未返回 check_date 时应回退到查询区间起始日。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "user_tasks": [
                        {
                            "employee_id": "wangwu@company.com",
                            "check_in_time": "2026-08-20T08:31:00+08:00",
                            "check_out_time": "2026-08-20T17:31:00+08:00",
                        }
                    ]
                },
            },
        )

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
        return httpx.Response(200, json={"code": 0, "data": {"user_tasks": []}})

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
        return httpx.Response(200, json={"code": 0, "data": {"user_tasks": []}})

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
        return httpx.Response(200, json={"code": 0, "data": {"user_tasks": []}})

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
