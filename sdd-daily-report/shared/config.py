"""配置读取与校验。

契约来源：specs/tasks.md Task 2 验收标准
    "config.py 能读取 config.yaml 并返回配置对象"
    "config.py 在配置缺少必填字段时抛出明确的错误信息"
设计来源：specs/design.md §6.2 安全约束
    "所有API密钥均通过环境变量注入 / 严禁在代码或配置文件中硬编码密钥"
    §6.3 配置热更新："成员映射表（config.yaml）修改后下次执行即可自动生效"
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from shared.errors import ConfigError


@dataclass(frozen=True)
class Member:
    """成员身份映射（对应书图 5-7）。"""

    name: str
    github: str
    lark: str


@dataclass(frozen=True)
class GitHubConfig:
    token: str | None
    repos: list[str]
    api_base: str = "https://api.github.com"
    timeout: float = 10.0
    max_retries: int = 3
    retry_interval: float = 5.0


@dataclass(frozen=True)
class LarkConfig:
    app_id: str | None
    app_secret: str | None
    project_id: str = ""
    chat_id: str = ""
    keywords: list[str] = field(default_factory=list)
    api_base: str = "https://open.feishu.cn/open-apis"
    timeout: float = 10.0
    max_retries: int = 3
    # 机器人推送目标 ID 的类型。默认按群推送（chat_id）；
    # 若飞书应用没有建群/群管理权限，可改为 open_id 走单聊推送
    # —— 实测 open_id 单聊只需 im:message:send_as_bot，无需 im:chat:create。
    receive_id_type: str = "chat_id"
    # 读取群聊消息历史的身份类型。
    # 实测：`im:message.group_msg` 在**应用身份**下也能生效（重新发布版本后可用），
    # 因此默认用 app，无需用户手动授权。
    # 若某些租户下该 scope 只对用户身份开放，可改为 user
    # 并先跑一次 `python -m collector.lark_oauth` 完成授权。
    auth_mode: str = "app"
    # 用户身份 token 的落盘位置（仅 auth_mode=user 时使用）
    user_token_file: str = "data/lark_user_token.json"


@dataclass(frozen=True)
class EmailConfig:
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str | None = None
    use_ssl: bool = True
    sender: str = ""
    recipients: list[str] = field(default_factory=list)
    max_retries: int = 2


@dataclass(frozen=True)
class ReportConfig:
    team_name: str = "研发团队"
    timezone: str = "Asia/Shanghai"
    sensitive_keywords: list[str] = field(default_factory=list)
    holidays: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AppConfig:
    members: list[Member]
    github: GitHubConfig
    lark: LarkConfig
    email: EmailConfig
    report: ReportConfig
    storage_path: str = "data/reports.db"

    def member_by_github(self, username: str) -> Member | None:
        for member in self.members:
            if member.github == username:
                return member
        return None

    def member_by_lark(self, lark_id: str) -> Member | None:
        for member in self.members:
            if member.lark == lark_id:
                return member
        return None


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value else None


def _require(raw: dict[str, Any], key: str, section: str) -> Any:
    if key not in raw or raw[key] in (None, ""):
        raise ConfigError(f"配置缺少必填字段：{section}.{key}")
    return raw[key]


def load_config(path: str | Path) -> AppConfig:
    """读取 config.yaml 并返回 AppConfig；缺失必填字段时抛 ConfigError。"""
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"配置文件不存在：{config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - defensive
        raise ConfigError(f"配置文件不是合法 YAML：{exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("配置文件顶层必须是映射（mapping）")

    # ---- members（必填）----
    raw_members = _require(raw, "members", "root")
    if not isinstance(raw_members, list) or not raw_members:
        raise ConfigError("配置缺少必填字段：members 必须是非空列表")
    members: list[Member] = []
    for index, item in enumerate(raw_members):
        if not isinstance(item, dict):
            raise ConfigError(f"members[{index}] 必须是映射")
        for key in ("name", "github", "lark"):
            if not item.get(key):
                raise ConfigError(f"配置缺少必填字段：members[{index}].{key}")
        members.append(Member(name=str(item["name"]), github=str(item["github"]), lark=str(item["lark"])))

    # ---- github ----
    raw_github = raw.get("github") or {}
    github = GitHubConfig(
        token=_env("GITHUB_TOKEN"),
        repos=[str(r) for r in raw_github.get("repos", [])],
        api_base=str(raw_github.get("api_base", "https://api.github.com")),
        timeout=float(raw_github.get("timeout", 10.0)),
        max_retries=int(raw_github.get("max_retries", 3)),
        retry_interval=float(raw_github.get("retry_interval", 5.0)),
    )

    # ---- lark ----
    raw_lark = raw.get("lark") or {}
    lark = LarkConfig(
        app_id=_env("LARK_APP_ID"),
        app_secret=_env("LARK_APP_SECRET"),
        project_id=str(raw_lark.get("project_id", "")),
        chat_id=str(raw_lark.get("chat_id", "")),
        keywords=[str(k) for k in raw_lark.get("keywords", [])],
        api_base=str(raw_lark.get("api_base", "https://open.feishu.cn/open-apis")),
        timeout=float(raw_lark.get("timeout", 10.0)),
        receive_id_type=str(raw_lark.get("receive_id_type", "chat_id")),
        auth_mode=str(raw_lark.get("auth_mode", "app")),
        user_token_file=_resolve_relative_path(
            raw_lark.get("user_token_file", "data/lark_user_token.json"), config_path
        ),
        max_retries=int(raw_lark.get("max_retries", 3)),
    )

    # ---- email ----
    raw_email = raw.get("email") or {}
    email = EmailConfig(
        smtp_host=str(raw_email.get("smtp_host", "")),
        smtp_port=int(raw_email.get("smtp_port", 465)),
        smtp_user=str(raw_email.get("smtp_user", "")),
        smtp_password=_env("SMTP_PASSWORD"),
        use_ssl=bool(raw_email.get("use_ssl", True)),
        sender=str(raw_email.get("sender", "")),
        recipients=[str(r) for r in raw_email.get("recipients", [])],
    )

    # ---- report ----
    raw_report = raw.get("report") or {}
    report = ReportConfig(
        team_name=str(raw_report.get("team_name", "研发团队")),
        timezone=str(raw_report.get("timezone", "Asia/Shanghai")),
        sensitive_keywords=[str(k) for k in raw_report.get("sensitive_keywords", [])],
        holidays=[str(d) for d in raw_report.get("holidays", [])],
    )

    return AppConfig(
        members=members,
        github=github,
        lark=lark,
        email=email,
        report=report,
        storage_path=_resolve_relative_path(
            raw.get("storage_path", "data/reports.db"), config_path
        ),
    )


def _resolve_relative_path(value: Any, config_path: Path) -> str:
    """把配置里的相对路径解析成相对**配置文件所在目录**的绝对路径。

    示例配置里写的是 ``storage_path: data/reports.db``、
    ``user_token_file: data/lark_user_token.json`` 这样的相对路径。它们必须相对
    配置文件本身解析，而不是相对进程的当前工作目录 —— 否则"从别处调用"就会把日报库
    或令牌文件写到调用者的 cwd 下：本项目合并成单仓库后，项目嵌在
    ``sdd-reproduction/sdd-daily-report/``，从仓库根执行
    ``python sdd-daily-report/main.py`` 是很自然的动作（VS Code 按 F5 或点运行按钮时
    cwd 就是工作区根目录），而那会把库写到 ``sdd-reproduction/data/reports.db``。

    绝对路径原样返回，不使用相对语义。
    """
    path = Path(str(value))
    if path.is_absolute():
        return str(path)
    return str((config_path.resolve().parent / path).resolve())
