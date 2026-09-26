"""实连验证：用真实凭据验证各外部依赖是否可用。

与 `--check` 的区别：
  - `--check`  只检查"配置里填了没有"（离线、秒回）
  - `--verify` 真的发起请求，验证凭据有效与接口可达（联网、会消耗配额）

为什么要单独做这个：本项目在接真实 GitHub 时就暴露过"Mock 全绿但真实接口不通"的
问题（`/commits` 列表接口不返回 stats/files，导致统计字段恒为 0）。
因此有了真凭据后，第一步必须是实连验证，而不是直接跑业务逻辑。

用法：
    python verify_connectivity.py              # 全部验证（含只读探测）
    python verify_connectivity.py --github     # 只验 GitHub
    python verify_connectivity.py --lark       # 只验飞书（含端点探查）
    python verify_connectivity.py --email      # 只验邮件（会真实发一封）
"""

from __future__ import annotations

import argparse
import io
import smtplib
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import httpx  # noqa: E402

from shared.config import AppConfig, load_config  # noqa: E402
from shared.models import DailyReport, MemberReport  # noqa: E402

CONFIG = "config.github.yaml"

OK = "✓"
NG = "✗"
WARN = "!"


def _section(title: str) -> None:
    print()
    print("=" * 66)
    print(title)
    print("=" * 66)


# ===================================================== GitHub

def verify_github(cfg: AppConfig) -> bool:
    _section("GitHub")
    if not cfg.github.token:
        print(f"  {WARN} 未设置 GITHUB_TOKEN —— 可继续，但匿名配额仅 60 次/小时")
    else:
        print(f"  · token 已设置（长度 {len(cfg.github.token)}）")

    headers = {"Accept": "application/vnd.github+json", "User-Agent": "sdd-verify"}
    if cfg.github.token:
        headers["Authorization"] = f"Bearer {cfg.github.token}"

    try:
        with httpx.Client(timeout=20, headers=headers) as client:
            # 1) 凭据是否有效
            response = client.get(f"{cfg.github.api_base}/user")
            if response.status_code == 200:
                login = response.json().get("login")
                print(f"  {OK} 凭据有效，登录身份：{login}")
            elif response.status_code == 401:
                if cfg.github.token:
                    print(f"  {NG} 凭据无效（401）：token 可能已过期或被撤销")
                    return False
                # 匿名访问 /user 本来就返回 401，这不代表出问题
                print(f"  {OK} 未用 token（匿名访问），/user 返回 401 属预期")
            else:
                print(f"  {WARN} /user 返回 HTTP {response.status_code}")

            # 2) 配额
            limits = client.get(f"{cfg.github.api_base}/rate_limit").json()
            core = limits["resources"]["core"]
            print(f"  {OK} 配额：{core['remaining']}/{core['limit']} 次/小时")

            # 3) 逐个仓库可达性
            ok = True
            for repo in cfg.github.repos:
                r = client.get(f"{cfg.github.api_base}/repos/{repo}")
                if r.status_code == 200:
                    data = r.json()
                    print(f"  {OK} 仓库可达：{repo}（默认分支 {data.get('default_branch')}）")
                else:
                    print(f"  {NG} 仓库不可达：{repo} → HTTP {r.status_code}")
                    ok = False
            return ok
    except Exception as exc:  # noqa: BLE001
        print(f"  {NG} 连接失败：{type(exc).__name__}: {exc}")
        return False


# ===================================================== 飞书

# 探测策略：**直接调用项目自己的采集器**，而不是另写一套请求。
# 教训：早期版本在这里重复构造请求，格式与 collector 各写一份，
# 结果 collector 修好了、探测脚本还停在旧格式，报告出错误的"格式错"结论。
# 现在探测用的就是真实代码路径，报告的结论即业务代码的真实表现。
LARK_TASK_CANDIDATES = [
    ("collector.lark_task（项目真实实现）", "task"),
]
LARK_MSG_CANDIDATES = [
    ("collector.lark_msg（项目真实实现）", "msg"),
]
LARK_ATTENDANCE_CANDIDATES = [
    ("collector.lark_attendance（项目真实实现）", "attendance"),
]


def _lark_token(cfg: AppConfig) -> str | None:
    """获取 tenant_access_token —— 这是飞书一切接口的前提。"""
    if not (cfg.lark.app_id and cfg.lark.app_secret):
        print(f"  {NG} 缺少 LARK_APP_ID / LARK_APP_SECRET，无法验证")
        return None
    print(f"  · app_id 已设置（长度 {len(cfg.lark.app_id)}）")
    try:
        response = httpx.post(
            f"{cfg.lark.api_base}/auth/v3/tenant_access_token/internal",
            json={"app_id": cfg.lark.app_id, "app_secret": cfg.lark.app_secret},
            timeout=20,
        )
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        print(f"  {NG} 获取 Token 时连接失败：{type(exc).__name__}: {exc}")
        return None

    if data.get("code") == 0:
        token = data.get("tenant_access_token", "")
        print(f"  {OK} 凭据有效，已获取 tenant_access_token（长度 {len(token)}，"
              f"有效期 {data.get('expire')}s）")
        return token
    print(f"  {NG} 获取 Token 失败：code={data.get('code')} msg={data.get('msg')}")
    print("      常见原因：app_id/app_secret 填错，或应用未启用")
    return None


def _classify(response, body: dict) -> tuple[str, str]:
    """把飞书响应归类为 (标记, 判读说明)。

    实测经验（2026-09）——错误码能精确区分问题性质：
      0                    成功
      99991672             缺权限（端点路径已解析成功）
      99992402             请求体字段校验失败 —— **请求格式错**
      122xxxx / 业务码      参数格式正确，但业务数据不满足（如没有员工数据）
      10002 / 404          端点可能不存在
    """
    code = body.get("code")
    msg = str(body.get("msg") or "")

    if response.status_code == 200 and code == 0:
        data = body.get("data") or {}
        return OK, f"成功，返回数据结构：{list(data.keys())}"

    if code == 99991672 or response.status_code == 403:
        return WARN, "Token 有效，但**应用缺少该接口的权限**（需在开放平台开通并发布）"

    if code in (99991663, 99991661):
        return WARN, "Token 失效"

    if code == 99992402:
        return NG, "**请求格式错**：字段校验失败 —— 参数位置/名称/类型需要按官方文档校准"

    if code == 10002 or response.status_code == 404:
        return NG, "**端点或参数格式不对**：路径可能不存在"

    # 122xxxx 是考勤/业务域的参数与数据错误码：格式对，但业务数据不满足
    if isinstance(code, int) and 1_220_000 <= code < 1_230_000:
        return OK, f"**请求格式正确**（业务级提示：{msg}）—— 参数构造无误，只是数据不满足"

    return WARN, f"未归类：HTTP {response.status_code} code={code} msg={msg}"


def _probe_collector(token: str, cfg: AppConfig, label: str, kind: str) -> None:
    """通过项目真实采集器探测某个飞书能力。

    只报告结果，不重复构造请求 —— 保证探测结论与业务代码一致。
    """
    from datetime import date, datetime, time, timezone

    from collector import lark_attendance, lark_msg, lark_task

    print(f"\n  ── {label} ──")
    day = date(2026, 4, 24)
    since = datetime.combine(day, time.min)
    until = datetime.combine(day, time(23, 59, 59))

    try:
        if kind == "task":
            result = lark_task.collect(
                cfg.lark.project_id, since, until, config=cfg.lark
            )
        elif kind == "msg":
            result = lark_msg.collect(
                cfg.lark.chat_id, cfg.lark.keywords, since, until,
                config=cfg.lark,
                sensitive_keywords=cfg.report.sensitive_keywords,
            )
        else:
            result = lark_attendance.collect(day, day, config=cfg.lark)
    except Exception as exc:  # noqa: BLE001
        print(f"    {NG} 调用采集器异常：{type(exc).__name__}: {exc}")
        return

    if result.success:
        print(f"    {OK} 采集成功，返回 {len(result.records)} 条记录")
        if not result.records:
            print("        说明：接口与权限均正常，只是该应用名下暂无此类数据")
        return

    error = result.error_message or ""
    if "99991672" in error or "Access denied" in error or "权限" in error:
        print(f"    {WARN} **缺少接口权限**：{error[:110]}")
        print("        需在开放平台开通对应 scope 并发布版本")
    elif "99992402" in error:
        print(f"    {NG} **请求格式错**：{error[:110]}")
        print("        参数位置/名称/类型需按官方文档校准")
    elif "invalid container_id" in error:
        print(f"    {OK} **请求格式正确**（{error[:70]}）")
        print("        端点与参数无误，只是未配置有效的 chat_id —— 消息历史只能按群查询，")
        print("        单聊会话不支持；需先把机器人拉进一个群再填入 chat_id")
    elif any(
        marker in error
        for marker in (
            "1220001", "1220002", "1220004", "1220005",
            "employeeNos is empty", "userIds all invalid",
        )
    ):
        detail = error.split("错误：")[-1][:60]
        print(f"    {OK} **请求格式正确**（业务级提示：{detail}）")
        print("        参数构造无误，只是缺少业务数据（该租户没有考勤员工记录）")
    else:
        print(f"    {WARN} 未归类：{error[:120]}")


def verify_lark(cfg: AppConfig) -> bool:
    _section("飞书开放平台")
    token = _lark_token(cfg)
    if token is None:
        return False

    _probe_collector(token, cfg, "任务接口（Task 4 用）", "task")
    _probe_collector(token, cfg, "消息接口（Task 5 用）", "msg")
    _probe_collector(token, cfg, "考勤接口（Task 11 用）", "attendance")

    print()
    print("  说明：以上是**只读探测**，通过项目真实采集器调用，不写任何数据。")
    print("        判读：成功/业务级提示 = 格式与权限都正确；缺权限 = 需开通 scope；")
    print("        请求格式错 = 参数需按官方文档校准。")
    return True


# ===================================================== 邮件

def verify_email(cfg: AppConfig) -> bool:
    _section("邮件（QQ SMTP）")
    if not cfg.email.smtp_host or not cfg.email.smtp_user:
        print(f"  {NG} 配置不完整：需要 smtp_host 与 smtp_user")
        return False
    if not cfg.email.smtp_password:
        print(f"  {NG} 未设置 SMTP_PASSWORD（QQ 邮箱 SMTP 授权码）")
        return False
    if not cfg.email.recipients:
        print(f"  {NG} 未配置收件人")
        return False

    print(f"  · {cfg.email.smtp_host}:{cfg.email.smtp_port}  发件人 {cfg.email.sender}")
    print(f"  · 收件人 {cfg.email.recipients}")
    try:
        with smtplib.SMTP_SSL(cfg.email.smtp_host, cfg.email.smtp_port, timeout=20) as server:
            server.login(cfg.email.smtp_user, cfg.email.smtp_password)
            print(f"  {OK} 登录成功（授权码有效）")
    except smtplib.SMTPAuthenticationError as exc:
        print(f"  {NG} 认证失败：{exc}")
        print("      常见原因：用的是 QQ 登录密码而不是 SMTP 授权码；或授权码已失效")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"  {NG} 连接失败：{type(exc).__name__}: {exc}")
        return False
    return True


def send_test_email(cfg: AppConfig) -> bool:
    """真实发送一封测试日报，用于确认端到端投递。"""
    from notifier.email import send

    report = DailyReport(
        date=date.today(),
        team_name=cfg.report.team_name,
        members=[MemberReport(name="连通性测试", github_username="-")],
        generated_at=datetime.now(),
        markdown="# 连通性测试\n\n这是一封由 verify_connectivity.py 发出的测试邮件。\n",
        html=(
            "<!DOCTYPE html><html><body>"
            "<h1>连通性测试</h1>"
            "<p>这是一封由 <code>verify_connectivity.py</code> 发出的测试邮件。</p>"
            "<p>收到即说明 SMTP 链路与 HTML 正文均正常。</p>"
            "</body></html>"
        ),
    )
    ok = send(report, cfg.email.recipients, config=cfg.email)
    if ok:
        print(f"  {OK} 测试邮件已发送至 {cfg.email.recipients}，请查收（含垃圾邮件箱）")
    else:
        print(f"  {NG} 测试邮件发送失败，详见上方日志")
    return ok


# ===================================================== main

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="实连验证各外部依赖")
    parser.add_argument("--config", default=CONFIG)
    parser.add_argument("--github", action="store_true", help="只验 GitHub")
    parser.add_argument("--lark", action="store_true", help="只验飞书（含端点探查）")
    parser.add_argument("--email", action="store_true", help="只验邮件")
    parser.add_argument("--send-test", action="store_true", help="额外真实发送一封测试邮件")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    only = args.github or args.lark or args.email

    results: dict[str, bool] = {}
    if not only or args.github:
        results["GitHub"] = verify_github(cfg)
    if not only or args.lark:
        results["飞书"] = verify_lark(cfg)
    if not only or args.email:
        results["邮件"] = verify_email(cfg)
        if args.send_test and results.get("邮件"):
            results["测试邮件"] = send_test_email(cfg)

    _section("汇总")
    for name, ok in results.items():
        print(f"  {OK if ok else NG} {name}")
    print()
    if all(results.values()):
        print("全部通过。可以跑真实业务：")
        print(f"  python main.py --config {args.config} --date <有数据的日期>")
        return 0
    print("存在未通过项，请先按上方提示修复。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
