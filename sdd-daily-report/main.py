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
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from collector import github as github_collector
from collector import lark_attendance, lark_msg, lark_task
from generator import formatter
from notifier import email as email_notifier
from notifier import lark_bot
from shared.calendar import is_working_day
from shared.config import AppConfig, load_config
from shared.errors import DailyReportError, NonWorkingDayError
from shared.logger import get_logger
from shared.models import AttendanceRecord, CollectResult, CommitRecord, MessageRecord, TaskRecord
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
                else lark_task.collect(
                    config.lark.project_id, since, until, config=config.lark
                )
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
            day, day, config=config.lark
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

    # 边界场景：识别非工作日并跳过（proposal.md §3.3）
    if not is_working_day(target_day, config.report.holidays):
        raise NonWorkingDayError(target_day.isoformat())

    results = collect_all(config, target_day, mock=mock, tasks_file=tasks_file)
    sources = {name: result.success for name, result in results.items()}

    if not any(result.success for result in results.values()):
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
    report = formatter.generate(
        members,
        target_day,
        config.report.team_name,
        sources=sources,
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


def load_tasks_file(path: str, day: date) -> CollectResult[TaskRecord]:
    """从 JSON 文件读取任务数据，转成与飞书采集器相同的 CollectResult[TaskRecord]。

    用途：无法使用飞书开放平台 API 时的本地兜底。例如手上只有一个飞书
    「个人待办」链接（applink.feishu.cn/client/todo/...），那属于客户端私有接口，
    开放平台读不到 —— 此时可以把任务内容写成 JSON 手动喂进来。

    **数据进入的路径与真实采集完全一致**：同样是 CollectResult[TaskRecord]，
    同样要过成员身份映射表，同样进聚合与日报生成。因此不存在"另一套逻辑"。

    文件格式（数组）：
        [
          {"assignee": "alice@example.com", "title": "写周报",
           "status_from": "进行中", "status_to": "已完成",
           "updated_at": "2026-04-24T16:30:00+08:00"}
        ]

    说明：
      - `assignee` 必须能通过 config.yaml 的成员映射表匹配到人，否则该条会被
        聚合层丢弃（与真实采集行为一致，不凭空造成员）
      - `updated_at` 可省略，默认取目标日期当天 12:00
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
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise DailyReportError(f"第 {index} 条任务必须是对象")
        missing = [key for key in ("assignee", "title") if not item.get(key)]
        if missing:
            raise DailyReportError(
                f"第 {index} 条任务缺少必填字段：{', '.join(missing)}"
            )

        raw_time = item.get("updated_at")
        if raw_time:
            try:
                updated = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
            except ValueError as exc:
                raise DailyReportError(
                    f"第 {index} 条任务的 updated_at 不是合法时间：{raw_time}"
                ) from exc
        else:
            updated = datetime.combine(day, time(12, 0))

        records.append(
            TaskRecord(
                assignee=str(item["assignee"]),
                title=str(item["title"]),
                status_from=str(item.get("status_from", "")),
                status_to=str(item.get("status_to", "")),
                updated_at=updated,
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
            status_from="进行中",
            status_to="已完成",
            updated_at=stamp(16, 30),
        ),
        TaskRecord(
            assignee=names_lark[1],
            title="GitHub 采集模块联调",
            status_from="待开始",
            status_to="进行中",
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
            employee_id=names_lark[0],
            date=day,
            check_in=stamp(9, 32),
            check_out=stamp(19, 5),
            work_hours=9.5,
            status="正常",
        ),
        AttendanceRecord(
            employee_id=names_lark[1],
            date=day,
            check_in=stamp(10, 15),
            check_out=stamp(19, 10),
            work_hours=8.9,
            status="迟到",
        ),
    ]

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
        )
    except NonWorkingDayError as exc:
        logger.info("跳过日报生成", extra={"source": "main", "reason": str(exc)})
        print(str(exc))
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
