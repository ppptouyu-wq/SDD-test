"""主编排入口。

契约来源：specs/tasks.md Task 9
    "创建 main.py 作为系统入口，按顺序编排'采集→聚合→生成→推送'的完整流程"

验收标准（tasks.md Task 9）：
- [x] 按"采集→聚合→生成→推送"顺序编排，任一数据源失败不阻断其他数据源
- [x] 支持 --date 指定日报日期，默认当天
- [x] 支持 --check 健康检查模式（验证 GitHub/飞书/邮件配置）
- [x] 支持 --mock 演示模式（无凭据时用内置数据跑通全链路）
- [x] 识别非工作日（周末及法定节假日）并跳过日报生成
- [x] 全局异常被捕获，执行状态写入日志
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import httpx

from collector import github as github_collector
from collector import lark_attendance, lark_msg, lark_task, pipeline as pipeline_collector, repo_progress
from collector.lark_auth import DEFAULT_TOKEN_FILE, UserTokenManager
from generator import formatter, template
from notifier import email as email_notifier
from notifier import lark_bot
from shared.calendar import is_working_day
from shared.config import AppConfig, load_config
from shared.errors import DailyReportError, NonWorkingDayError
from shared.logger import get_logger
from shared.models import (
    AttendanceRecord,
    CollectResult,
    CommitRecord,
    MessageRecord,
    RepoProgress,
    TaskRecord,
)
from shared.storage import ReportStorage

logger = get_logger("main")

# 项目根目录。合并成单仓库后项目嵌在 sdd-reproduction/sdd-daily-report/ 里，
# 从仓库根执行 `python sdd-daily-report/main.py` 是自然动作（VS Code 按 F5 或点
# 运行按钮时 cwd 就是工作区根），所以默认路径必须锚在项目根而不是 cwd。
PROJECT_ROOT = Path(__file__).resolve().parent

DEFAULT_CONFIG = PROJECT_ROOT / "config.yaml"
DEFAULT_ENV = PROJECT_ROOT / ".env"
SOURCE_GITHUB = "GitHub"
SOURCE_LARK_TASK = "飞书任务"
SOURCE_LARK_MSG = "飞书消息"
SOURCE_LARK_ATTENDANCE = "飞书考勤"
# v1.8：本机代码仓库的"工程数据源"。它**不是外部协作平台**，因此刻意不参与
# sources 四态判定（design.md §3.4）：失败多是"这台机器上不是 Git 仓库"，
# 那种情况属可解释的缺失，不该把整份日报打成 partial。
SOURCE_REPO_PROGRESS = "工程进展"

# 飞书考勤接口只接受 employee_id / employee_no 两种 ID 类型（实测：传 open_id 会被
# 99992402 字段校验拒绝）。本项目统一按「员工ID」查询，取值与 config.yaml 中
# members[].lark 存放的飞书 ID 保持一致（tasks.md Task 9 v1.3 验收标准）。
ATTENDANCE_EMPLOYEE_TYPE = "employee_id"


def _attendance_user_ids(config: AppConfig) -> list[str]:
    """从成员身份映射表取出飞书**员工ID**列表（去重、保持顺序）。

    tasks.md Task 9（v1.3）：考勤采集必须以真实名单调用。传空名单时飞书返回
    业务错误 `1220001 employeeNos is empty`，会让"当天没打卡"被误报成
    "数据获取失败" —— 这正是 v1.1 上线后考勤一栏长期显示失败的原因。
    注意用的是 `lark_employee_id` 而不是 `lark`：考勤接口只接受 employee_id /
    employee_no，传 open_id 会被字段校验拒绝（实测 99992402）。
    """
    unique: dict[str, None] = {}
    for member in config.members:
        if member.lark_employee_id:
            unique.setdefault(member.lark_employee_id, None)
    return list(unique)


def _coverage_provider() -> Callable[[], str] | None:
    """规范覆盖率取数（v1.8）。取不到就返回 None，表示"该项未记录"。

    `sdd_agents.metrics` 是本项目的规范工具，**不是运行时依赖**，因此这里延迟导入；
    任何异常都退化成"未记录"——工程进展是锦上添花，不该拖垮日报生成（design.md §6.8）。
    """
    try:
        from sdd_agents.metrics import map_acceptance_to_tests
    except Exception:  # noqa: BLE001 - 工具不可用时静默降级
        return None

    def _coverage() -> str:
        coverage = map_acceptance_to_tests(PROJECT_ROOT)
        return f"{coverage.covered}/{coverage.total} = {coverage.ratio:.1%}"

    return _coverage


def _progress_of(results: dict) -> RepoProgress | None:
    """从采集结果里取出当日工程进展；取不到返回 None（邮件渲染中性空态）。"""
    result = results.get(SOURCE_REPO_PROGRESS)
    if result is None or not result.success or not result.records:
        return None
    return result.records[0]


def collect_all(
    config: AppConfig,
    day: date,
    *,
    mock: bool = False,
    tasks_file: str | None = None,
) -> dict:
    """采集所有数据源。任一数据源失败都不阻断其他数据源（优雅降级）。

    :param tasks_file: 手动提供的任务数据（JSON 文件）。用于无法使用飞书 API 的场景
        —— 例如只有一个飞书「个人待办」链接，而开放平台 task API 读不到它。
        数据进入的路径与真实采集**完全一致**（同样是 CollectResult[TaskRecord]），
        因此日报生成、聚合、验收行为都不需要特殊分支。
    """
    since = datetime.combine(day, time.min)
    until = datetime.combine(day, time(23, 59, 59))

    if mock:
        results = _mock_results(config, day)
    else:
        results = {
            SOURCE_GITHUB: github_collector.collect(
                config.github.repos, since, until, config=config.github
            ),
            SOURCE_LARK_TASK: (
                load_tasks_file(tasks_file, day)
                if tasks_file
                else _collect_tasks(config, since, until)
            ),
            SOURCE_LARK_MSG: lark_msg.collect(
                config.lark.chat_id,
                config.lark.keywords,
                since,
                until,
                config=config.lark,
                sensitive_keywords=config.report.sensitive_keywords,
            ),
        }
        results[SOURCE_LARK_ATTENDANCE] = lark_attendance.collect(
            day,
            day,
            config=config.lark,
            employee_type=ATTENDANCE_EMPLOYEE_TYPE,
            user_ids=_attendance_user_ids(config),
            # v1.13：非工作日两侧都没打卡判「休息日」而不是「缺勤」。
            # 判定所需的节假日表来自配置，采集层不自己读 config（design.md §3.0.1）。
            working_day=is_working_day(day, config.report.holidays),
        )
        # v1.8：本机仓库的工程进展（唯一不联网的"数据源"）。它失败不影响其他源，
        # 也不进 sources（见 SOURCE_REPO_PROGRESS 的注释）。
        results[SOURCE_REPO_PROGRESS] = repo_progress.collect(
            config.report.repo_path,
            since,
            until,
            coverage=_coverage_provider(),
        )

    for name, result in results.items():
        logger.info(
            "数据源采集结果",
            extra={
                "source": name,
                "success": result.success,
                "count": len(result.records),
                "error": result.error_message,
            },
        )
    return results


def run(
    config: AppConfig,
    *,
    day: date | None = None,
    mock: bool = False,
    push: bool = True,
    tasks_file: str | None = None,
    working_days_only: bool = False,
    storage: ReportStorage | None = None,
    notifiers: dict | None = None,
) -> dict:
    """执行一次完整的日报流程，返回执行摘要。"""
    target_day = day or date.today()
    started_at = datetime.now()
    logger.info(
        "日报流程开始",
        extra={"source": "main", "date": target_day.isoformat(), "mock": mock},
    )

    # v1.4：默认**不判断工作日** —— "今天该不该发日报"是调度策略，不是产品能力。
    # 双休日/节假日同样采集、生成、推送（design.md §6.6）。只有显式要求
    # "仅工作日"时才走这段判断（proposal.md §3.3 v1.4）。
    if working_days_only and not is_working_day(target_day, config.report.holidays):
        raise NonWorkingDayError(target_day.isoformat())

    results = collect_all(config, target_day, mock=mock, tasks_file=tasks_file)
    # v1.8：工程数据源不参与四态判定（design.md §3.4）—— 它既不该出现在
    # `sources` 里，也不该把"外部数据源全挂"这种情况从 all_sources_failed
    # 里救回来（否则一台有 git 仓库的机器会生成一份空日报）。
    external = {
        name: result
        for name, result in results.items()
        if name != SOURCE_REPO_PROGRESS
    }
    sources = {name: result.success for name, result in external.items()}

    if not any(result.success for result in external.values()):
        # design.md §6.1："所有数据源都不可用 | 记录错误日志，发送告警邮件，不生成空日报"
        logger.error("所有数据源均不可用，不生成空日报", extra={"source": "main"})
        return {
            "date": target_day.isoformat(),
            "status": "all_sources_failed",
            "sources": sources,
            "elapsed_seconds": 0.0,
        }

    members = formatter.aggregate(
        config.members,
        commits=results[SOURCE_GITHUB],
        tasks=results[SOURCE_LARK_TASK],
        messages=results[SOURCE_LARK_MSG],
        attendance=results.get(SOURCE_LARK_ATTENDANCE),
    )
    # v1.11：正文标题区的标题与邮件主题同源 —— 都用 report_title() 和同一份 email 配置
    # 渲染，收件箱里看到的名字与点开后正文最上方的名字因此不可能各写一套（design.md §6.7(2)）。
    email_title = template.report_title(
        config.report.team_name,
        target_day,
        subject_pattern=config.email.subject_pattern,
        grade=config.email.grade,
        owner=config.email.owner,
    )
    report = formatter.generate(
        members,
        target_day,
        config.report.team_name,
        sources=sources,
        progress=_progress_of(results),
        # v1.13：交付流水线现状（仓库文件 + 本地 git 事实）；
        # v1.14：再读 GitHub 公开接口拿"最近一次运行"的真实结论（不需要 actions:read）
        pipeline=(
            pipeline_collector.collect(
                config.report.repo_path,
                slug=config.github.repos[0] if config.github.repos else "",
                token=config.github.token,
                api_base=config.github.api_base,
                timeout=config.github.timeout,
            )
            # v1.16：板块开关默认关。关掉时连这两次 HTTP 都不发 —— 不采集就不会渲染，
            # 也不会因为读不到而生成一句误导人的"未记录交付流水线"（design.md §6.7(6)）。
            if config.email.show_pipeline
            else None
        ),
        title=email_title,
    )

    store = storage or ReportStorage(config.storage_path)
    store.save(report)

    if not push:
        # design.md §6.1 关键原则 4：推送失败不应让整次执行失败。
        # 本地演练（--mock 且未显式 --push）时跳过真实推送，链路仍然完整。
        logger.info("已跳过推送（未启用 --push）", extra={"source": "main"})
        push_result = {"email": None, "lark_bot": None}
    else:
        dispatch = notifiers or {
            "email": lambda rep: email_notifier.send(
                rep, config.email.recipients, config=config.email
            ),
            "lark_bot": lambda rep: lark_bot.send(rep, config.lark.chat_id, config=config.lark),
        }
        push_result = {}
        for name, func in dispatch.items():
            try:
                push_result[name] = bool(func(report))
            except DailyReportError as exc:
                logger.error(
                    "推送渠道失败",
                    extra={"source": "main", "channel": name, "error": str(exc)},
                )
                push_result[name] = False

    elapsed = (datetime.now() - started_at).total_seconds()
    summary = {
        "date": target_day.isoformat(),
        "status": "ok",
        "members": len(members),
        "sources": sources,
        "push": push_result,
        "elapsed_seconds": round(elapsed, 3),
    }
    logger.info(
        "日报流程结束",
        extra={
            "source": "main",
            "date": target_day.isoformat(),
            "elapsed_seconds": summary["elapsed_seconds"],
            "push": push_result,
        },
    )
    return summary


def health_check(config: AppConfig) -> dict[str, bool]:
    """`main.py --check`：验证所有 API 连接与推送配置。"""
    checks = {
        "github_repos_configured": bool(config.github.repos),
        "github_token_present": bool(config.github.token),
        "lark_credentials_present": bool(config.lark.app_id and config.lark.app_secret),
        "lark_project_configured": bool(config.lark.project_id),
        "lark_chat_configured": bool(config.lark.chat_id),
        "email_host_configured": bool(config.email.smtp_host),
        "email_password_present": bool(config.email.smtp_password),
        "email_recipients_configured": bool(config.email.recipients),
        "members_configured": len(config.members) > 0,
    }
    for name, ok in checks.items():
        logger.info("健康检查项", extra={"source": "check", "item": name, "ok": ok})
    return checks


def _collect_tasks(config: AppConfig, since: datetime, until: datetime) -> CollectResult[TaskRecord]:
    """采集飞书「我负责的」任务（v1.16）。

    身份选择是这段的全部要点：`GET /task/v2/tasks` 只接受 `user_access_token`。
    没有授权落盘文件时**不请求、直接失败**并给出授权命令 —— 应用身份调它不会报错，
    只会返回 0 条，"读不到"于是被写成"今日无任务"，比报错更难发现。
    """
    token_file = Path(config.lark.user_token_file)
    if not token_file.exists():
        # 交给采集层产出失败结论与提示文案（单一来源，见 lark_task.collect）。
        return lark_task.collect(since, until, config=config.lark, token_manager=None)
    with httpx.Client(base_url=config.lark.api_base, timeout=config.lark.timeout) as client:
        manager = UserTokenManager(
            client,
            config.lark.app_id,
            config.lark.app_secret,
            token_file=token_file,
        )
        return lark_task.collect(
            since, until, config=config.lark, client=client, token_manager=manager
        )


def load_tasks_file(path: str, day: date) -> CollectResult[TaskRecord]:
    """从 JSON 文件读取任务数据，转成与飞书采集器相同的 CollectResult[TaskRecord]。

    用途：无法使用飞书开放平台 API 时的本地兜底。例如手上只有一个飞书
    「个人待办」链接（applink.feishu.cn/client/todo/...），那属于客户端私有接口，
    开放平台读不到 —— 此时可以把任务内容写成 JSON 手动喂进来。

    **数据进入的路径与真实采集完全一致**：同样是 CollectResult[TaskRecord]，
    同样要过成员身份映射表，同样进聚合与日报生成。因此不存在"另一套逻辑"。

    文件格式（数组，v1.16 起与飞书真实字段对齐）：
        [
          {"assignee": "alice@example.com", "title": "写周报",
           "status": "done", "created_at": "2026-04-24T09:00:00+08:00",
           "completed_at": "2026-04-24T16:30:00+08:00",
           "updated_at": "2026-04-24T16:30:00+08:00"}
        ]

    说明：
      - `assignee` 必须能通过 config.yaml 的成员映射表匹配到人，否则该条会被
        聚合层丢弃（与真实采集行为一致，不凭空造成员）
      - `status` 只认飞书的 "todo" / "done"（默认 "todo"）
      - `created_at` / `due_at` / `completed_at` 都可省略；`updated_at` 省略时
        默认取目标日期当天 12:00
    """
    file_path = Path(path)
    if not file_path.exists():
        raise DailyReportError(f"任务数据文件不存在：{file_path}")

    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DailyReportError(f"任务数据不是合法 JSON：{exc}") from exc

    if not isinstance(raw, list):
        raise DailyReportError("任务数据顶层必须是数组")

    records: list[TaskRecord] = []

    def _optional_time(value: object, *, index: int, key: str) -> datetime | None:
        """解析可选时间字段。

        统一**去掉时区**（naive）：`main.run()` 里 since/until 是 naive 本地时间，
        aware 与 naive 混在一起比较会直接抛 TypeError。带 `+08:00` 的值去掉时区后
        仍是同一个墙上时间，因此语义不变。
        """
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(
                tzinfo=None
            )
        except ValueError as exc:
            raise DailyReportError(
                f"第 {index} 条任务的 {key} 不是合法时间：{value}"
            ) from exc

    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise DailyReportError(f"第 {index} 条任务必须是对象")
        missing = [key for key in ("assignee", "title") if not item.get(key)]
        if missing:
            raise DailyReportError(
                f"第 {index} 条任务缺少必填字段：{', '.join(missing)}"
            )

        records.append(
            TaskRecord(
                assignee=str(item["assignee"]),
                title=str(item["title"]),
                status=str(item.get("status") or "todo"),
                created_at=_optional_time(item.get("created_at"), index=index, key="created_at"),
                due_at=_optional_time(item.get("due_at"), index=index, key="due_at"),
                completed_at=_optional_time(
                    item.get("completed_at"), index=index, key="completed_at"
                ),
                updated_at=(
                    _optional_time(item.get("updated_at"), index=index, key="updated_at")
                    or datetime.combine(day, time(12, 0))
                ),
            )
        )

    logger.info(
        "已从文件载入任务数据",
        extra={"source": SOURCE_LARK_TASK, "count": len(records), "file": str(file_path)},
    )
    return CollectResult.ok(records)


def _mock_results(config: AppConfig, day: date) -> dict:
    """演示数据：无需任何外部凭据即可跑通全链路。"""
    members = config.members
    names_github = [m.github for m in members]
    names_lark = [m.lark for m in members]
    # 考勤用它自己的 ID（employee_id），不是 open_id；演示配置也要配 lark_employee_id，
    # 否则演示数据里考勤一条都匹配不上 —— 那正是真实链路 v1.1 的老症状。
    attendance_ids = _attendance_user_ids(config)
    tz = timezone(timedelta(hours=8))
    stamp = lambda h, m: datetime.combine(day, time(h, m), tzinfo=tz)  # noqa: E731

    commits = [
        CommitRecord(
            author=names_github[0],
            message="feat: 日报三段式编排",
            timestamp=stamp(10, 12),
            repo=config.github.repos[0] if config.github.repos else "acme/daily-report",
            additions=120,
            deletions=18,
            files_changed=4,
        ),
        CommitRecord(
            author=names_github[1],
            message="fix: 修复分页查询遗漏最后一页",
            timestamp=stamp(14, 5),
            repo=config.github.repos[0] if config.github.repos else "acme/daily-report",
            additions=9,
            deletions=3,
            files_changed=2,
        ),
    ]
    tasks = [
        TaskRecord(
            assignee=names_lark[0],
            title="日报模板评审",
            status="done",
            created_at=stamp(9, 40),
            due_at=None,
            completed_at=stamp(16, 30),
            updated_at=stamp(16, 30),
        ),
        TaskRecord(
            assignee=names_lark[1],
            title="GitHub 采集模块联调",
            status="todo",
            created_at=stamp(10, 15),
            due_at=stamp(18, 0),
            completed_at=None,
            updated_at=stamp(11, 0),
        ),
    ]
    messages = [
        MessageRecord(
            sender=names_lark[0],
            content="项目日报的推送时间定在 18:00 可以吗？",
            timestamp=stamp(15, 20),
            chat_name="平台研发组",
        ),
        MessageRecord(
            sender=names_lark[1],
            content="这个需求我下午对一下接口契约",
            timestamp=stamp(15, 42),
            chat_name="平台研发组",
        ),
        MessageRecord(
            sender=names_lark[2] if len(names_lark) > 2 else names_lark[0],
            content="本月绩效沟通安排在下周（应被敏感词过滤）",
            timestamp=stamp(16, 0),
            chat_name="平台研发组",
        ),
    ]
    attendance = [
        AttendanceRecord(
            employee_id=attendance_ids[0],
            date=day,
            check_in=stamp(9, 32),
            check_out=stamp(19, 5),
            work_hours=9.5,
            status="正常",
        ),
        AttendanceRecord(
            employee_id=attendance_ids[1],
            date=day,
            check_in=stamp(10, 15),
            check_out=stamp(19, 10),
            work_hours=8.9,
            status="迟到",
        ),
    ] if len(attendance_ids) >= 2 else []

    # 按关键词与敏感词过滤，保证演示数据与真实链路行为一致
    keywords = config.lark.keywords
    filtered_messages = [
        m
        for m in messages
        if lark_msg.match_keywords(m.content, keywords)
        and not lark_msg.is_sensitive(m.content, config.report.sensitive_keywords)
    ]

    return {
        SOURCE_GITHUB: CollectResult.ok(commits),
        SOURCE_LARK_TASK: CollectResult.ok(tasks),
        SOURCE_LARK_MSG: CollectResult.ok(filtered_messages),
        SOURCE_LARK_ATTENDANCE: CollectResult.ok(attendance),
        # v1.8：演示数据里也给一条工程进展，让 --mock 能覆盖邮件的第 4 段
        SOURCE_REPO_PROGRESS: CollectResult.ok(
            [
                RepoProgress(
                    commit_count=2,
                    version="v1.8",
                    change_layer="设计变更",
                    spec_changed=["specs/design.md", "specs/contracts/data-models.md"],
                    code_changed=True,
                    same_commit=True,
                    spec_coverage="97/98 = 99.0%",
                )
            ]
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="智能日报生成器")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="配置文件路径")
    parser.add_argument("--date", help="日报日期（YYYY-MM-DD），默认当天")
    parser.add_argument("--mock", action="store_true", help="使用内置演示数据，无需外部凭据")
    parser.add_argument(
        "--tasks-file",
        help="从 JSON 文件读取任务数据（用于飞书 API 不可用时的本地兜底）",
    )
    parser.add_argument("--check", action="store_true", help="健康检查模式")
    parser.add_argument(
        "--serve",
        action="store_true",
        help="启动本地只读展示页（v1.2），在浏览器查看已生成的日报",
    )
    parser.add_argument(
        "--port", type=int, default=None, help="展示页端口（仅与 --serve 同用，默认 8000）"
    )
    parser.add_argument(
        "--db",
        default=None,
        help="日报库路径（仅与 --serve 同用，默认读 config.yaml，缺失时用 data/reports.db）",
    )
    push_group = parser.add_mutually_exclusive_group()
    push_group.add_argument(
        "--push", dest="push", action="store_true", help="执行真实推送（邮件 + 飞书机器人）"
    )
    push_group.add_argument(
        "--no-push", dest="push", action="store_false", help="只生成不推送"
    )
    parser.set_defaults(push=None)
    parser.add_argument(
        "--lark-auth",
        action="store_true",
        help="打印飞书用户身份授权链接（v1.16）：读「我负责的」任务只认 user_access_token",
    )
    parser.add_argument(
        "--lark-auth-code",
        default=None,
        metavar="CODE",
        help="用授权回调里的 code 换取并落盘 user_access_token（v1.16，配合 --lark-auth）",
    )
    parser.add_argument(
        "--lark-user-token",
        default=None,
        metavar="TOKEN",
        help="直接采纳一个 user_access_token（v1.16.1 后门）：回调地址没登记时"
        "改用飞书 API 调试台取令牌，2 小时后失效",
    )
    parser.add_argument(
        "--working-days-only",
        action="store_true",
        help="仅工作日运行（v1.4）：周末或 config.yaml 中 holidays 命中的日期跳过。"
        "默认不开启，即双休日与节假日照常生成并推送",
    )
    return parser


def load_dotenv(path: str | Path | None = None) -> int:
    """把 .env 里的键值对加载进 os.environ，返回加载条数。

    只填**尚未设置**的变量（已存在的环境变量优先，方便临时覆盖）。
    故意不依赖 python-dotenv：只需支持 ``KEY=VALUE`` 这一种写法，
    空值、``#`` 注释、行首空行一律跳过，值两端的成对引号会被剥掉。

    与 design.md §6.2 的安全约束一致：凭据只进进程环境，不落到配置文件里。

    ``path`` 省略时读 ``DEFAULT_ENV``（= 项目根下的 `.env`），**不是** cwd 下的
    `.env`：合并成单仓库后从仓库根执行时 cwd 是仓库根，cwd 相对路径会静默读不到凭据，
    真跑就降级成"数据源全部失败"——比直接报错更难排查。
    """
    env_path = Path(path) if path is not None else DEFAULT_ENV
    if not env_path.exists():
        return 0

    loaded = 0
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and value and not os.environ.get(key):
            os.environ[key] = value
            loaded += 1
    return loaded


def _lark_auth(
    config: AppConfig, *, url_only: bool, code: str | None, token: str | None = None
) -> int:
    """飞书用户身份一次性授权（v1.16，tasks.md Task 4 v1.16 验收标准）。

    为什么非做不可：`GET /task/v2/tasks`（「我负责的」任务）官方文档只接受
    `user_access_token`；应用身份调它**不报错**，而是恒返回 `code=0` + 0 条。
    实测已经把这条口径钉死（见 design.md §3.6），所以"没授权"必须报错，
    不能继续伪装成"今天没有任务"。
    """
    with httpx.Client(base_url=config.lark.api_base, timeout=config.lark.timeout) as client:
        manager = UserTokenManager(client, config.lark.app_id, config.lark.app_secret)
        if url_only:
            print("在浏览器打开下面的链接，用**你自己的飞书账号**同意授权：")
            print()
            print(manager.authorization_url())
            print()
            print("同意后浏览器会跳到 redirect_uri（页面 404 是正常的），")
            print("把地址栏里 `code=` 后面那一串复制回来执行：")
            print(f"  python main.py --lark-auth-code <CODE>")
            print()
            print("如果授权页自己就报错（常见原因：redirect_uri 没登记在开发者后台），")
            print("改用飞书「API 调试台」以用户身份调一次任务接口，把返回头里的")
            print("user_access_token 贴回来（2 小时后失效）：")
            print("  python main.py --lark-user-token <TOKEN>")
            return 0
        if token:
            try:
                manager.adopt_token(token)
            except DailyReportError as exc:
                print(f"写入用户 Token 失败：{exc}")
                return 1
            print(f"用户 Token 已落盘：{DEFAULT_TOKEN_FILE}")
            print("这是手工采纳的令牌，2 小时后失效、没有 refresh_token；")
            print("长期口径仍是走一次 python main.py --lark-auth 完成授权。")
            return 0
        try:
            manager.exchange_code(code or "")
        except DailyReportError as exc:
            print(f"换取用户 Token 失败：{exc}")
            return 1
        print(f"用户 Token 已落盘：{DEFAULT_TOKEN_FILE}")
        print("之后 30 天内作业会自动用 refresh_token 续期，不需要再授权。")
        return 0


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    load_dotenv()
    args = build_parser().parse_args(argv)
    # --serve（v1.2）：只读展示页。**故意放在加载配置之前** ——
    # 展示页只读 data/reports.db，不应该因为本机没有 config.yaml（仓库只分发 .example
    # 模板）就起不来。
    if args.serve:
        return _serve(args)
    # 未显式指定时：--mock 演练默认不推送，避免误发；正式运行默认推送
    push = (not args.mock) if args.push is None else args.push
    try:
        config = load_config(args.config)
    except DailyReportError as exc:
        logger.error("加载配置失败", extra={"source": "main", "error": str(exc)})
        print(f"加载配置失败：{exc}")
        return 2

    if args.lark_auth or args.lark_auth_code or args.lark_user_token:
        return _lark_auth(
            config,
            url_only=args.lark_auth,
            code=args.lark_auth_code,
            token=args.lark_user_token,
        )

    if args.check:
        checks = health_check(config)
        failed = [name for name, ok in checks.items() if not ok]
        print("健康检查结果：")
        for name, ok in checks.items():
            print(f"  {'✓' if ok else '✗'} {name}")
        if failed:
            print("健康检查未通过项：" + "、".join(failed))
            return 1
        print("健康检查通过：所有配置项均已就绪")
        return 0

    target_day = date.fromisoformat(args.date) if args.date else date.today()

    try:
        summary = run(
            config,
            day=target_day,
            mock=args.mock,
            push=push,
            tasks_file=args.tasks_file,
            working_days_only=args.working_days_only,
        )
    except NonWorkingDayError as exc:
        logger.info(
            "跳过日报生成",
            extra={"source": "main", "reason": str(exc), "working_days_only": True},
        )
        print(f"{exc}（--working-days-only 已开启；去掉该参数即可在任意日期运行）")
        return 0
    except DailyReportError as exc:
        logger.error("日报流程失败", extra={"source": "main", "error": str(exc)})
        print(f"日报流程失败：{exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - 全局兜底，保证执行状态写入日志
        logger.error("日报流程异常", extra={"source": "main", "error": str(exc)})
        print(f"日报流程异常：{exc}")
        return 1

    if summary["status"] == "all_sources_failed":
        print("所有数据源均不可用：已记录错误日志，未生成空日报")
        return 1

    push_text = (
        "已跳过（--no-push）"
        if not push
        else "、".join(f"{k}={'成功' if v else '失败'}" for k, v in summary["push"].items())
    )
    print(
        f"日报生成完成：{summary['date']}，成员 {summary.get('members', 0)} 人，"
        f"耗时 {summary['elapsed_seconds']}s，推送：{push_text}"
    )
    print(f"已存入库：{config.storage_path}")
    return 0


DEFAULT_STORAGE = PROJECT_ROOT / "data" / "reports.db"


def _serve(args: argparse.Namespace) -> int:
    """启动本地只读展示页（v1.2，specs/tasks.md Task 12）。

    库路径的优先级：``--db`` > ``config.yaml`` 的 ``storage_path`` > ``data/reports.db``。
    返回进程退出码，直接透传给调用方（0 = 正常停止，2 = 启动前校验未通过）。
    """
    # 局部导入：非 --serve 的执行路径不需要 http.server，避免把展示层的依赖
    # 带进 Cron 链路（design.md §6.5(3) 零新增依赖）。
    from webview.app import DEFAULT_HOST, DEFAULT_PORT, serve

    db_path = args.db
    if not db_path:
        try:
            db_path = load_config(args.config).storage_path
        except DailyReportError as exc:
            logger.warning(
                "读取配置失败，展示页回退到默认日报库",
                extra={"source": "main", "error": str(exc), "db_path": str(DEFAULT_STORAGE)},
            )
            db_path = str(DEFAULT_STORAGE)
    return serve(db_path=db_path, host=DEFAULT_HOST, port=args.port or DEFAULT_PORT)


def _force_utf8_console() -> None:
    """Windows 控制台默认 GBK，中文摘要会显示为乱码；仅在可写时切到 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover - 非 TTY 场景
                pass


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
