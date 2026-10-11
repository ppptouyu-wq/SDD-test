"""主入口（main.py）测试：编排、CLI 参数与手动任务数据通道。

对应书 7.5.6 Step 5「全量回归测试」列出的 `tests/test_main.py`。

覆盖范围：
- `main.run()` 的编排行为：mock 演练、推送开关、数据源状态汇总
- `main.main()` 命令行入口的退出码
- `main.load_dotenv()` 凭据注入（让 `.env.example` 的承诺真正生效；默认读项目根下的 `.env`）
- `main.load_tasks_file()` 手动任务数据通道（飞书 API 读不到任务时的兜底）
- 路径锚定：默认配置 / 默认库路径 / 相对 `storage_path` 不随进程 cwd 漂移

端到端全链路（采集→聚合→生成→推送）的联调测试见 `tests/test_integration.py`。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import main as app
from shared.config import load_config
from shared.models import CollectResult
from shared.storage import ReportStorage

DAY = date(2026, 8, 20)  # 周四
TZ_OFFSET = timedelta(hours=8)


# ============================================ 演练模式（--mock）

def test_mock_mode_runs_full_pipeline_without_credentials(config):
    """回归：--mock 演练路径（内置演示数据）必须能独立跑通。

    这条路不经过采集层 Mock，曾因 main._mock_results 引用未导入的
    CollectResult 而整体抛 NameError，且没有测试覆盖。
    """
    from shared.models import CollectResult

    results = app._mock_results(config, DAY)

    assert set(results) == {"GitHub", "飞书任务", "飞书消息", "飞书考勤"}
    assert all(isinstance(r, CollectResult) and r.success for r in results.values())
    assert len(results["GitHub"].records) == 2
    # 演示数据里的"绩效"消息必须已被敏感词过滤掉
    assert all(
        "绩效" not in m.content for m in results["飞书消息"].records
    )


def test_run_with_mock_flag_end_to_end(config):
    """`main.run(mock=True, push=False)` 全链路不得抛异常，且能生成并落库。"""
    with ReportStorage(config.storage_path) as storage:
        summary = app.run(config, day=DAY, mock=True, push=False, storage=storage)
        row = storage.get(DAY)

    assert summary["status"] == "ok"
    assert summary["members"] == 3
    assert summary["push"] == {"email": None, "lark_bot": None}
    assert row is not None
    # 三个核心板块 + 考勤段都应在日报里
    assert "### 代码提交" in row["markdown"]
    assert "### 考勤" in row["markdown"]


# ============================================ 命令行入口

def test_cli_mock_returns_zero_exit_code(config, tmp_dir, monkeypatch):
    """命令行入口 `main([...])` 在 --mock 下应返回 0。"""
    config_path = tmp_dir / "config.yaml"
    config_path.write_text(_minimal_yaml(config), encoding="utf-8")

    code = app.main(
        ["--config", str(config_path), "--date", DAY.isoformat(), "--mock"]
    )

    assert code == 0


# ============================================ 凭据注入（.env）

def test_load_dotenv_reads_keys(tmp_dir, monkeypatch):
    """`.env` 里的 KEY=VALUE 应注入进程环境（修复 .env.example 承诺却不生效的问题）。"""
    monkeypatch.delenv("SDD_TEST_SMTP", raising=False)
    monkeypatch.delenv("SDD_TEST_TOKEN", raising=False)
    path = tmp_dir / ".env"
    path.write_text(
        "# 注释行\n"
        "\n"
        "SDD_TEST_SMTP = 'auth code with spaces'\n"
        'SDD_TEST_TOKEN="quoted-token"\n'
        "SDD_TEST_EMPTY=\n",
        encoding="utf-8",
    )

    loaded = app.load_dotenv(path)

    assert loaded == 2, "空值不应计入，注释与空行应跳过"
    assert app.os.environ["SDD_TEST_SMTP"] == "auth code with spaces", "成对引号应被剥掉"
    assert app.os.environ["SDD_TEST_TOKEN"] == "quoted-token"
    assert "SDD_TEST_EMPTY" not in app.os.environ, "空值不得写入环境"


def test_load_dotenv_does_not_override_existing_env(tmp_dir, monkeypatch):
    """已存在的环境变量优先，便于命令行临时覆盖 .env 里的值。"""
    monkeypatch.setenv("SDD_TEST_SMTP", "from-shell")
    path = tmp_dir / ".env"
    path.write_text("SDD_TEST_SMTP=from-file\n", encoding="utf-8")

    loaded = app.load_dotenv(path)

    assert loaded == 0
    assert app.os.environ["SDD_TEST_SMTP"] == "from-shell"


def test_load_dotenv_missing_file_is_noop(tmp_dir):
    """没有 .env 时静默返回 0，不能抛异常（凭据全靠环境变量的用户不受影响）。"""
    assert app.load_dotenv(tmp_dir / "nope.env") == 0


def _minimal_yaml(config) -> str:
    # Windows 路径的反斜杠会被 YAML 当作转义序列，统一转成正斜杠
    storage = str(config.storage_path).replace("\\", "/")
    return f"""
github:
  repos: {list(config.github.repos)!r}
lark:
  project_id: "{config.lark.project_id}"
  chat_id: "{config.lark.chat_id}"
  keywords: {list(config.lark.keywords)!r}
report:
  team_name: "{config.report.team_name}"
  sensitive_keywords: {list(config.report.sensitive_keywords)!r}
storage_path: "{storage}"
members:
  - name: "张三"
    github: "zhangsan"
    lark: "zhangsan@company.com"
  - name: "李四"
    github: "lisi-dev"
    lark: "lisi@company.com"
  - name: "王五"
    github: "wangwu"
    lark: "wangwu@company.com"
"""


# ============================================ 手动任务数据通道
# 用途：只有飞书「个人待办」链接、开放平台 API 读不到任务内容时，
# 允许把任务写进 JSON 手动喂进来。数据路径与真实采集完全一致。


def test_load_tasks_file_parses_records(tmp_dir):
    path = tmp_dir / "tasks.json"
    path.write_text(
        json.dumps(
            [
                {
                    "assignee": "lisi@company.com",
                    "title": "写周报",
                    "status_from": "进行中",
                    "status_to": "已完成",
                    "updated_at": "2026-04-24T16:30:00+08:00",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = app.load_tasks_file(str(path), DAY)

    assert result.success is True
    assert len(result.records) == 1
    record = result.records[0]
    assert record.assignee == "lisi@company.com"
    assert record.title == "写周报"
    assert record.status_from == "进行中"
    assert record.status_to == "已完成"
    assert record.updated_at.year == 2026


def test_load_tasks_file_defaults_updated_at_to_day(tmp_dir):
    path = tmp_dir / "tasks.json"
    path.write_text(
        json.dumps(
            [{"assignee": "lisi@company.com", "title": "无时间任务"}], ensure_ascii=False
        ),
        encoding="utf-8",
    )

    record = app.load_tasks_file(str(path), DAY).records[0]

    assert record.updated_at.date() == DAY


@pytest.mark.parametrize(
    "payload, expect_message",
    [
        ('{"x": 1}', "顶层必须是数组"),
        ('[{"title": "缺 assignee"}]', "assignee"),
        ('[{"assignee": "a@b.com"}]', "title"),
        ('[{"assignee": "a@b.com", "title": "x", "updated_at": "不是时间"}]', "updated_at"),
        ("[123]", "必须是对象"),
    ],
)
def test_load_tasks_file_rejects_bad_input(tmp_dir, payload, expect_message):
    path = tmp_dir / "tasks.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(app.DailyReportError) as excinfo:
        app.load_tasks_file(str(path), DAY)

    assert expect_message in str(excinfo.value)


def test_load_tasks_file_missing_file_raises(tmp_dir):
    with pytest.raises(app.DailyReportError) as excinfo:
        app.load_tasks_file(str(tmp_dir / "nope.json"), DAY)
    assert "不存在" in str(excinfo.value)


# ============================================ 路径锚定（合并成单仓库后的回归）


def test_project_paths_are_anchored_at_the_project_root_not_cwd():
    """默认配置与默认库路径必须锚在**项目根**，而不是进程的 cwd。

    回归测试：三个项目合并成 `sdd-reproduction/` 单仓库后，本项目嵌在
    `sdd-reproduction/sdd-daily-report/` 里。从仓库根执行
    `python sdd-daily-report/main.py`（在 VS Code 里点编辑器右上角的运行按钮就是
    这种调用方式，此时 cwd 是工作区根）时，cwd 相对路径会找不到 config.yaml，
    实测报：`加载配置失败：[config] 配置文件不存在：config.yaml`。
    """
    project_root = Path(__file__).resolve().parent.parent

    assert Path(app.PROJECT_ROOT) == project_root
    assert Path(app.DEFAULT_CONFIG).is_absolute()
    assert Path(app.DEFAULT_CONFIG) == project_root / "config.yaml"
    assert Path(app.DEFAULT_ENV).is_absolute()
    assert Path(app.DEFAULT_ENV) == project_root / ".env"
    assert Path(app.DEFAULT_STORAGE).is_absolute()
    assert Path(app.DEFAULT_STORAGE) == project_root / "data" / "reports.db"


def test_load_dotenv_defaults_to_the_project_env_not_cwd(tmp_dir, monkeypatch):
    """`load_dotenv()` 不传参数时读项目根下的 `.env`，不读 cwd 下的 `.env`。

    回归测试：默认参数曾写作 `path=".env"`（cwd 相对）。从仓库根执行时 cwd 是仓库根，
    于是本项目真正那份 `.env` 被**静默**忽略，真跑降级成"数据源全部失败" —— 比直接报错
    更难排查，因为它看起来像是凭据本身有问题。
    """
    env_file = tmp_dir / "project.env"
    env_file.write_text("SDD_TEST_DEFAULT_ENV=yes\n", encoding="utf-8")
    monkeypatch.delenv("SDD_TEST_DEFAULT_ENV", raising=False)

    elsewhere = tmp_dir / "elsewhere"
    elsewhere.mkdir()
    # 在无关目录里放一份"诱饵" .env，确认它不会被读到
    (elsewhere / ".env").write_text("SDD_TEST_DEFAULT_ENV=wrong\n", encoding="utf-8")
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(app, "DEFAULT_ENV", env_file)

    assert app.load_dotenv() == 1
    assert app.os.environ["SDD_TEST_DEFAULT_ENV"] == "yes"


def test_relative_storage_path_resolves_against_the_config_file(tmp_dir, monkeypatch):
    """配置里的相对 `storage_path` 相对**配置文件所在目录**解析，不随 cwd 漂移。

    回归测试：三个配置文件写的都是 `storage_path: "data/reports.db"`。如果按 cwd
    解析，从仓库根运行时日报库会被建到 `sdd-reproduction/data/reports.db`
    （项目外面），而不是项目自己的 `data/` 下 —— 展示页与 `--check` 会读不到刚写的库。
    """
    project = tmp_dir / "proj"
    project.mkdir()
    config_path = project / "config.yaml"
    config_path.write_text(
        """
lark:
  project_id: "proj_1"
  chat_id: "oc_1"
storage_path: "data/reports.db"
members:
  - name: "张三"
    github: "zhangsan"
    lark: "zhangsan@company.com"
""",
        encoding="utf-8",
    )

    elsewhere = tmp_dir / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)  # 故意在一个与项目毫不相干的目录下加载配置

    config = load_config(config_path)

    resolved = Path(config.storage_path)
    assert resolved.is_absolute(), "相对 storage_path 必须被解析成绝对路径"
    assert resolved == project / "data" / "reports.db"
    assert not str(resolved).startswith(str(elsewhere))


# ============================================ v1.3：考勤名单接线（Task 9 / Task 11）


def test_collect_all_passes_the_configured_employee_ids_to_attendance(tmp_dir, monkeypatch):
    """考勤必须以真实名单调用，且 ID 取自 `lark_employee_id`（不是 `lark`）。

    回归背景（v1.1 遗留）：`collect_all` 曾以
    `lark_attendance.collect(day, day, config=config.lark)` 调用，
    请求体里 `user_ids` 是空列表，飞书回业务错误
    `1220001 employeeNos is empty`，于是"当天确实没打卡"被误报成"数据获取失败"。
    另外 `lark` 存的是 open_id，而考勤接口只接受 employee_id / employee_no，
    两个 ID 必须分开取。
    """
    project = tmp_dir / "proj"
    project.mkdir()
    config_path = project / "config.yaml"
    config_path.write_text(
        """
lark:
  project_id: "proj_1"
  chat_id: "oc_1"
members:
  - name: "张三"
    github: "zhangsan"
    lark: "ou_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    lark_employee_id: "1001"
  - name: "李四"
    github: "lisi-dev"
    lark: "ou_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    lark_employee_id: "1002"
  - name: "王五"
    github: "wangwu"
    lark: "ou_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    lark_employee_id: "1001"
  - name: "赵六"
    github: "zhaoliu"
    lark: "ou_cccccccccccccccccccccccccccccccc"
""",
        encoding="utf-8",
    )
    config = load_config(config_path)

    seen: dict = {}

    def fake_attendance(since, until, **kwargs):
        seen.update(kwargs)
        return CollectResult.ok([])

    monkeypatch.setattr(app.lark_attendance, "collect", fake_attendance)
    for name in ("github_collector", "lark_task", "lark_msg"):
        monkeypatch.setattr(
            getattr(app, name), "collect", lambda *a, **k: CollectResult.ok([])
        )

    app.collect_all(config, date(2026, 10, 9))

    assert seen["employee_type"] == "employee_id"
    # 去重且保序：张三/王五共用 1001 只查一次；没配员工ID的赵六不产生空串
    assert seen["user_ids"] == ["1001", "1002"]
    assert seen["config"] is config.lark


def test_attendance_user_ids_is_empty_when_nobody_has_an_employee_id(tmp_dir):
    """全员漏配 `lark_employee_id` 时名单为空 —— 这种情况采集层必须显式失败。

    这条不是"允许空名单"，而是把"空名单"这个可观测事实钉住：任务书 Task 9（v1.3）
    要求空名单必须以 `success=False` 暴露，不能静默返回空结果（否则又变回
    "没打卡"伪装成"数据获取失败"的老 bug 的反面 —— 空结果伪装成"正常且无记录"）。
    """
    project = tmp_dir / "proj"
    project.mkdir()
    config_path = project / "config.yaml"
    config_path.write_text(
        """
lark:
  project_id: "proj_1"
  chat_id: "oc_1"
members:
  - name: "张三"
    github: "zhangsan"
    lark: "ou_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert app._attendance_user_ids(config) == []

