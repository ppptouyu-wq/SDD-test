# 智能日报生成器 — 架构设计

> 本文件是 `specs/proposal.md` 的架构解法。书中（印刷 p.102-103）给出的是**结构概要**，
> 此处按该书第 5 章提供的细节（图 5-4 模块职责、图 5-6 数据模型、图 5-7 身份映射、
> 图 5-8 接口契约、ADR-001/002、图 5-9/5-10/5-11 非功能性约束）补全为可执行版本。
> 补全原则：只填空缺，不改变书中已明确的决策。

## 1. 系统架构

- 管道式架构：采集层 → 生成层 → 推送层 + 共享基础层
- [架构图]

```
                    ┌──────────────────────────────────────────┐
    GitHub API ─┐   │  采集层 Collector                         │
  飞书任务 API ─┼──▶│  github / lark_task / lark_msg            │──┐
  飞书消息 API ─┘   └──────────────────────────────────────────┘  │ 原始数据(JSON)
                                                                  ▼
┌──────────────────────────────────────────┐   ┌──────────────────────────────┐
│  生成层 Generator                         │◀──│  MemberReport 聚合            │
│  formatter / template                     │   └──────────────────────────────┘
└──────────────────────────────────────────┘
                    │ 日报(Markdown / HTML)
                    ▼
        ┌──────────────────────────────────────────┐
        │  推送层 Notifier                          │
        │  email(SMTP) / lark_bot(webhook)          │──▶ 团队 Leader 邮箱 + 飞书群
        └──────────────────────────────────────────┘

  共享基础层 shared/：配置管理 | 日志 | 错误处理 | 数据存储(SQLite)
```

**为什么是管道式架构（书中的否决理由）**
- 不采用微服务：`proposal.md` 已界定"不需要常驻服务"及 5 人团队规模，引入微服务只会徒增复杂性。
- 不采用事件驱动：数据流呈清晰的线性、同步特征，不存在复杂的事件分发与异步解耦需求。
- 黄金法则：选择能满足需求的最简架构（"一切应尽可能简单，但不能过于简单"）。

## 2. 模块职责

| 模块 | 职责 | 不负责 |
|---|---|---|
| `collector/`<br>`github.py`<br>`lark_task.py`<br>`lark_msg.py` | 从外部 API 获取原始数据：GitHub Commit 数据、飞书任务变更数据、飞书群消息数据 | 不做数据格式化；不做去重判断；不做推送 |
| `generator/`<br>`formatter.py`<br>`template.py` | 将原始数据组织为日报：数据整理 + Markdown 生成；日报模板管理 | 不做数据采集；不做 API 调用；不做推送 |
| `notifier/`<br>`email.py`<br>`lark_bot.py` | 将日报推送给目标：SMTP 邮件发送；飞书机器人消息推送 | 不做数据处理；不做日报生成；不做数据采集 |
| `shared/`<br>`config.py`<br>`logger.py`<br>`errors.py`<br>`storage.py` | 跨模块的共享能力：配置读取和校验；统一日志格式；自定义异常 + 错误处理策略；SQLite 存储（日报历史） | — |
| `main.py` | 编排入口：按顺序调用三层；处理全局异常，记录执行状态 | — |

> **"不负责"列与 `proposal.md` 的"不做什么"一脉相承**。若缺乏负面约束，AI 倾向于把相关功能
> 就近并入当前模块（在 `collector` 里混入格式化逻辑、在 `notifier` 里植入数据查询），导致边界模糊。
> 指导原则：单一职责原则 —— 每个模块应当有且仅有一个引发变更的理由。

## 3. 数据模型

只承载语义，不绑定实现（用 dataclass 还是 Pydantic 属实现细节，留给 AI 裁量）。
本项目实现选择 `dataclasses`（stdlib，零依赖）。

```
CommitRecord（代码提交记录）
  author: str            # 提交者（GitHub用户名）
  message: str           # 提交信息（Commit Message）
  timestamp: datetime    # 提交时间
  repo: str              # 仓库名称
  additions: int         # 新增行数
  deletions: int         # 删除行数
  files_changed: int     # 变更文件数

TaskRecord（任务变更记录）
  assignee: str          # 负责人（飞书用户名）
  title: str             # 任务标题
  status_from: str       # 原状态
  status_to: str         # 新状态
  updated_at: datetime   # 变更时间

MessageRecord（消息记录）
  sender: str            # 发送者（飞书用户名）
  content: str           # 消息内容（纯文本）
  timestamp: datetime    # 发送时间
  chat_name: str         # 群名称

MemberReport（成员报告）
  name: str                         # 成员姓名
  github_username: str              # GitHub用户名
  commits: list[CommitRecord]       # 代码提交记录
  tasks: list[TaskRecord]           # 任务变更记录
  messages: list[MessageRecord]     # 相关消息记录
  attendance: AttendanceRecord | None  # 考勤记录（v1.1 新增）

DailyReport（每日报告）
  date: date                        # 日报日期
  team_name: str                    # 团队名称
  members: list[MemberReport]       # 各成员们的日报段落
  generated_at: datetime            # 生成时间
  markdown: str                     # 完整的Markdown格式日报
  html: str                         # 完整的HTML格式日报
```

### 3.0 新增数据模型（AttendanceRecord，v1.1 迭代新增）

```text
AttendanceRecord（考勤记录）

| 字段        | 类型              | 说明                                |
|-------------|-------------------|-------------------------------------|
| employee_id | str               | 飞书用户 ID                          |
| date        | date              | 考勤日期                             |
| check_in    | datetime \| None  | 签到时间                             |
| check_out   | datetime \| None  | 签退时间                             |
| work_hours  | float             | 工时（小时）                         |
| status      | str               | 正常 / 迟到 / 早退 / 缺勤 / 休假       |
```

字段级契约与状态取值见 `contracts/data-models.md`；本节的 `AttendanceRecord` 与
`MemberReport.attendance` 是 v1.1 迭代在架构层新增的部分。

### 3.1 跨系统身份映射

`CommitRecord.author` 是 GitHub 用户名，`TaskRecord.assignee` 是飞书用户名，二者必须通过
配置中的映射表关联，否则无法按"人"聚合。见 `config.yaml`：

```yaml
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
```

## 4. 接口契约

模块的外部接口必须屏蔽内部实现细节。契约必须先于任何一行代码定义。

```python
# 采集层接口
github.collect(
    repos: list[str],          # 仓库列表
    since: datetime,           # 起始时间
    until: datetime            # 结束时间
) -> list[CommitRecord]        # 返回提交记录列表

lark_task.collect(
    project_id: str,           # 飞书项目ID
    since: datetime,           # 起始时间
    until: datetime            # 结束时间
) -> list[TaskRecord]          # 返回任务变更列表

lark_msg.collect(
    chat_id: str,              # 群ID
    keywords: list[str],       # 过滤关键词
    since: datetime,           # 起始时间
    until: datetime            # 结束时间
) -> list[MessageRecord]       # 返回消息记录列表

# 采集层接口（v1.1 新增）
lark_attendance.collect(
    since: date,               # 起始日期
    until: date                # 结束日期
) -> CollectResult[AttendanceRecord]   # 返回考勤记录（含 success 标志位）

# 生成层接口
generator.generate(
    members: list[MemberReport],   # 各成员数据
    date: date,                    # 日报日期
    team_name: str                 # 团队名称
) -> DailyReport                   # 返回日报

# 推送层接口
email.send(
    report: DailyReport,           # 日报对象
    recipients: list[str]          # 收件人邮箱列表
) -> bool                          # 成功/失败

lark_bot.send(
    report: DailyReport,           # 日报对象
    chat_id: str                   # 目标群ID
) -> bool                          # 成功/失败
```

字段级契约见 `specs/contracts/data-models.md`。

## 5. 技术选型（ADR）

- ADR-001：HTTP 客户端 → httpx
- ADR-002：数据存储 → SQLite
- ADR-003：采集层返回 `CollectResult`（v1.1 由逆向回溯新增，见 `adrs/003-采集层返回CollectResult.md`）

### 5.1 飞书考勤接口的请求格式（实测校准，2026-09）

`POST /attendance/v1/user_tasks/query` 的参数分两处承载，且日期类型特殊。
初版实现三处都写错，实测恒返回 `99992402 field validation failed`：

| 参数 | 位置 | 类型 | 说明 |
|---|---|---|---|
| `employee_type` | **query string** | string | `employee_no`（工号）或 `employee_id` |
| `user_ids` | 请求体 | array[string] | 员工工号 / ID 列表 |
| `check_date_from` | 请求体 | **integer** | `YYYYMMDD`，如 `20260424` |
| `check_date_to` | 请求体 | **integer** | 同上 |

**校准判据**：格式正确后返回的是**业务级错误**（如 `1220001 employeeNos is empty`），
而不是格式级错误 `99992402`。错误码的变化即格式正确的证据。

同时约定：飞书在 HTTP 400 时也返回结构化 `code`/`msg`，
采集层**必须原样透传**，不得只抛 `"HTTP 400"` —— 否则会丢掉可用于定位问题的业务提示。

### 5.2 飞书机器人推送目标类型

`notifier.send` 的目标 ID 类型由 `LarkConfig.receive_id_type` 决定：

| 取值 | 含义 | 所需权限 |
|---|---|---|
| `chat_id`（默认） | 推送到群 | `im:message:send_as_bot` + 群管理权限（建群需 `im:chat:create`） |
| `open_id` | 单聊推送 | **只需** `im:message:send_as_bot` |

实测结论：应用若没有 `im:chat:create`，无法用 API 建群；
此时改用 `open_id` 单聊即可跑通推送，无需额外权限。

## 6. 非功能性约束

### 6.1 错误处理：优雅降级
部分故障不应导致整个系统停摆（单台发动机故障不应导致整架飞机坠毁）。

| 场景 | 处理方式 |
|---|---|
| GitHub API超时 | 重试 3 次（间隔 5s），若仍失败则标记"数据获取失败" |
| 飞书Token过期 | 自动刷新 Token 后重试 1 次 |
| 单个数据源完全不可用 | 跳过该数据源，日报中标注"XX 数据源暂不可用"，其他数据源正常采集 |
| 所有数据源都不可用 | 记录错误日志，发送告警邮件，不生成空日报 |
| 邮件发送失败 | 重试 2 次，若仍失败则记录日志和飞书消息告警 |
| 飞书推送失败 | 重试 2 次，若仍失败则记录日志和邮件告警 |

关键原则：
1. 采集层的失败不应阻塞生成层
2. 生成层的失败不应阻塞推送层（需要推送错误报告）
3. 任何失败都必须有日志记录
4. 推送渠道应互为备份告警通道

> 与 `proposal.md` § 3.2 的对应：`CollectResult.success=False` + `error_message`
> 即为"显式标注数据获取失败、严禁静默跳过"的实现手段（ADR-003）。

### 6.2 安全性

```
1. 密钥管理
   - 所有API密钥均通过环境变量注入
   - 严禁在代码或配置文件中硬编码密钥
   - 加入env文件，则必须加入.gitignore

2. 数据安全
   - 对飞书消息中的敏感关键词（如薪资、绩效、裁员等）进行过滤（配置黑名单）
   - 日报中不包含代码差异的具体内容（仅包含统计数据，如+10行/-5行）

3. 访问控制
   - 仅采集配置文件中明确列出的仓库或群组
   - 不采集配置范围之外的任何数据
```

### 6.3 可运维性

```
1. 日志
   - 格式：采用JSON lines（便于日志分析工具解析）
   - 级别：分为INFO（正常流程）+ERROR（异常）
   - 每次执行记录：开始时间、结束时间、各数据源采集条数及推送结果

2. 健康检查
   - 提供main.py --check模式：
     验证所有API连接是否正常
     验证邮件配置是否正确
     验证飞书机器人权限是否正常
   - 建议部署后每日执行一次

3. 配置热更新
   - 成员映射表（config.yaml）修改后下次执行即可自动生效，不需要重启服务
   - 新增或删除成员时不需要修改代码
```

### 6.4 测试与演示模式（本复现新增的实现约定，不影响书中决策）

- 全部外部 API 通过注入的 data source 抽象访问，单元测试用 Mock 替代真实调用
  （对应 `tasks.md` 中"使用 Mock 数据的单元测试全部通过"的验收标准）。
- `python main.py --mock` 使用内置演示数据跑通全链路，用于无凭据环境下的端到端验证。
- `main.py --check` 执行健康检查，`main.py --date YYYY-MM-DD` 指定日报日期。
