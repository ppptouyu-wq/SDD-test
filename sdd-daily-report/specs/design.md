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
                    ├────────────────────────────┐
                    ▼                            ▼
        ┌──────────────────────────┐   ┌──────────────────────────────┐
        │  推送层 Notifier          │   │  展示层 Web（v1.2 新增）      │
        │  email / lark_bot         │   │  只读 HTTP 服务 + 静态页面    │
        └──────────────────────────┘   └──────────────────────────────┘
                    │                            │
                    ▼                            ▼
        团队 Leader 邮箱 + 飞书群        浏览器 http://127.0.0.1:<port>
                                                 ▲
                                                 │ 只读
                                        ┌──────────────────┐
                                        │ data/reports.db  │
                                        └──────────────────┘

  共享基础层 shared/：配置管理 | 日志 | 错误处理 | 数据存储(SQLite)
```

**为什么是管道式架构（书中的否决理由）**
- 不采用微服务：`proposal.md` 已界定"不需要常驻服务"及 5 人团队规模，引入微服务只会徒增复杂性。
- 不采用事件驱动：数据流呈清晰的线性、同步特征，不存在复杂的事件分发与异步解耦需求。
- 黄金法则：选择能满足需求的最简架构（"一切应尽可能简单，但不能过于简单"）。
- v1.2 展示层不是"第四个管道阶段"，而是挂在存储层之上的一条**只读旁路**：
  它不接采集、不触发生成、不写库（见 §6.5）。

## 2. 模块职责

| 模块 | 职责 | 不负责 |
|---|---|---|
| `collector/`<br>`github.py`<br>`lark_task.py`<br>`lark_msg.py` | 从外部 API 获取原始数据：GitHub Commit 数据、飞书任务变更数据、飞书群消息数据 | 不做数据格式化；不做去重判断；不做推送 |
| `generator/`<br>`formatter.py`<br>`template.py` | 将原始数据组织为日报：数据整理 + Markdown 生成；日报模板管理 | 不做数据采集；不做 API 调用；不做推送 |
| `notifier/`<br>`email.py`<br>`lark_bot.py` | 将日报推送给目标：SMTP 邮件发送；飞书机器人消息推送 | 不做数据处理；不做日报生成；不做数据采集 |
| `shared/`<br>`config.py`<br>`logger.py`<br>`errors.py`<br>`storage.py` | 跨模块的共享能力：配置读取和校验；统一日志格式；自定义异常 + 错误处理策略；SQLite 存储（日报历史） | — |
| `webview/`（v1.2 新增）<br>`app.py`<br>`views.py`<br>`static/` | 只读展示日报：把存储层的日报记录转换成页面可渲染的视图模型；提供本地只读 HTTP 服务与静态页面 | **不写库**；不做数据采集；不做日报生成；不做推送；不做登录与权限；不做搜索与图表 |
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
  sources: dict[str, bool]          # 各数据源采集成功/失败（v1.2 新增，键为数据源名）
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
    lark: "ou_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"   # 飞书 open_id
    lark_employee_id: "1001"                       # 飞书员工ID（仅考勤用）
  - name: "李四"
    github: "lisi-dev"
    lark: "ou_yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy"
    lark_employee_id: "1002"
  - name: "王五"
    github: "wangwu"
    lark: "ou_zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"
    lark_employee_id: "1003"
```

**为什么飞书要拆成两个字段（v1.3 迭代修正）**：飞书各接口要求的身份标识并不统一 ——
任务/消息接口给出的是 `open_id`，而考勤接口 `attendance/v1/user_tasks/query` 只接受
`employee_id` / `employee_no`（实测传 `open_id` 被 `99992402` 字段校验直接拒绝，
传邮箱则报 `1220001 userIds all invalid`）。v1.1 只定义了单个 `lark` 字段，
既没有把名单传给考勤采集（空名单 → `employeeNos is empty`），也没有区分两种 ID 类型，
于是"当天没打卡"被误报成"数据获取失败"。v1.3 把考勤用的 ID 拆成独立字段
`lark_employee_id`，`lark` 继续服务 `TaskRecord.assignee` / `MessageRecord.sender`。

### 3.2 展示层视图模型（v1.2 迭代新增）

`webview/` 对外传递的**不是**内部领域对象，而是两种视图模型（书第 5 章"接口契约"原则：
模块边界要靠显式数据形状固定下来）。字段级契约见 `contracts/data-models.md` §3。

```text
ReportListItem（列表项）
  date: date              # 日报日期
  team_name: str          # 团队名称
  generated_at: datetime  # 生成时间
  member_count: int       # 成员数（列表页只显示计数，不加载成员明细）
  status: str             # ok / partial / failed / unknown（由 sources 推导，见下）

ReportDetail（详情）
  list_item: ReportListItem         # 复用列表项字段
  members: list[MemberView]         # 每位成员的四段内容
  sources: list[SourceView]         # 各数据源采集结果
  generated_at: datetime            # 生成时间

MemberView（成员视图）
  name: str                          # 成员姓名
  github_username: str               # GitHub 用户名
  commits: list[CommitView]          # 代码提交（无记录时为空列表）
  tasks: list[TaskView]              # 任务进展（无记录时为空列表）
  messages: list[MessageView]        # 协作沟通（无记录时为空列表）
  attendance: AttendanceView | None  # 考勤；None 表示"考勤数据暂不可用"

SourceView（数据源视图）
  name: str         # github / lark_task / lark_msg / lark_attendance
  success: bool     # 该数据源当日是否采集成功
  error: str | None # 失败原因；成功时为 None
```

**`status` 的推导规则（四种状态互斥且可判定）**

| 取值 | 判定条件 | 页面表现 |
|---|---|---|
| `ok` | `sources` 非空且全部为 `true` | 正常展示，无告警标签 |
| `partial` | 至少一个 `false`、且至少一个 `true` | 顶部告警条 + 失败数据源标注"数据获取失败" |
| `failed` | 全部为 `false` | 顶部错误告警条 |
| `unknown` | `sources` 为空（v1.2 之前的历史记录未记录来源） | 中性徽标"未记录数据源状态"：既不声称成功，也不误报为"全部失败" |

> `unknown` 的由来：初版推导规则把"`sources` 为空"并入 `failed`，结果是 v1.2 之前生成的历史日报
> 会被标注成"数据源全部失败"——而那天可能四个数据源全部成功，只是当时没有记录来源。
> **把"没记录"说成"全部失败"是撒谎**，故按图 7-8"规范缺陷 → 回溯更新规范"拆出第四态（见 ADR-004 补充记录）。
>
> 与 `proposal.md` §3.1 验收标准的对应：`partial`/`failed` 即"采集失败的数据源必须显式标注"
> 与"严禁静默跳过"在展示层的落地手段；`attendance=None` 则对应"考勤数据暂不可用"。
> **"今日无记录"是另一个维度**：它指某成员某板块为空列表，与数据源失败无关，两者不得互相顶替。

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

# 展示层接口（v1.2 新增）
webview.list_reports(
    storage: ReportStorage,        # 存储层（只读使用）
    limit: int = 365               # 最多列出条数
) -> list[ReportListItem]          # 按日期倒序返回列表项

webview.get_report(
    storage: ReportStorage,        # 存储层（只读使用）
    day: date                      # 日报日期
) -> ReportDetail | None           # 该日详情；无记录返回 None

webview.serve(
    db_path: str = "data/reports.db",  # 日报数据库路径
    host: str = "127.0.0.1",           # 只允许本机回环地址
    port: int = 8000                   # 本机端口
) -> int                               # 启动只读 HTTP 服务（阻塞）；返回进程退出码 0=正常停止，2=启动失败
```

字段级契约见 `specs/contracts/data-models.md`；HTTP 端点契约见 `specs/contracts/api-spec.yaml` 的 `paths`。

## 5. 技术选型（ADR）

- ADR-001：HTTP 客户端 → httpx
- ADR-002：数据存储 → SQLite
- ADR-003：采集层返回 `CollectResult`（v1.1 由逆向回溯新增，见 `adrs/003-采集层返回CollectResult.md`）
- ADR-004：展示层技术栈与设计系统来源（v1.2 需求变更新增，见 `adrs/004-展示层技术选型.md`）

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

### 6.5 展示层约束（v1.2 需求变更新增）

**(1) 只读**：展示层对 `data/reports.db` 只做查询，不得出现任何写操作（不新增表、
不更新字段、不删除记录）；也不得触发生成或推送流程。违反此约束即超出 `proposal.md` §2.2。

**(2) 只监听回环地址**：服务必须绑定 `127.0.0.1`，禁止绑定 `0.0.0.0`；
不实现登录与权限，安全性由"只在本机可用"保证（`proposal.md` 技术约束）。

**(3) 运行时依赖为零新增**：展示层必须用 Python 标准库（`http.server` / `sqlite3` /
`json`）与静态 HTML/CSS 实现，不得引入 Web 框架、不得要求构建步骤（ADR-004）。
理由是 `proposal.md` 技术约束要求"复用团队现有技术栈"，而 Cron 定时链路
（采集→生成→推送）的依赖集合不应被展示页扩大。

**(4) 视觉同源（设计令牌）**：本复现的展示层视觉不是临时配色，而是采用
OpenDesign 的 `ant` 设计系统（企业级、数据密集）的令牌值，机器可读原件为
`design-tokens.json`（56 个令牌，`format: od-design-tokens/v1`）。
落地时按**语义**取用，即"令牌引用语义，而非语义迁就令牌"：

| 页面语义 | 令牌 | 取值 | 说明 |
|---|---|---|---|
| 页面背景 | `--bg` | `#ffffff` | 白色工作台面 |
| 分组底板 | `--surface` | `#f7f8fa` | 卡片/表头底色 |
| 正文文字 | `--fg` | `#1f1f1f` | 主文本 |
| 次级文字 | `--fg-2` | `#4b5563` | 说明、时间戳 |
| 弱化文字 | `--muted` | `#697386` | 辅助信息 |
| 分隔线 | `--border` / `--border-soft` | `#d9dce3` / `#eef0f4` | 边框与浅分隔 |
| 强调色（品牌） | `--accent` | `#d32029` | 仅用于标题重音与焦点环 |
| 成功（数据源正常） | `--success` | `#22a06b` | 状态标记 |
| 警告（部分数据源失败） | `--warn` | `#faad14` | `partial` 告警 |
| 危险（全部失败/错误） | `--danger` | `#cf1322` | `failed` 告警 |
| 中性（未记录数据源状态） | `--muted` + `--border` | `#697386` / `#d9dce3` | `unknown` 徽标：无彩色，不参与告警色阶 |
| 圆角 | `--radius-sm` / `--radius-md` | `6px` / `10px` | 卡片与按钮 |
| 字号阶梯 | `--text-xs`…`--text-2xl` | `12/14/16/18/22/32px` | 见令牌原文 |
| 间距阶梯 | `--space-1`…`--space-8` | `4/8/12/16/20/24/32px` | 8px 基准 |
| 正文字号 | `--text-base` | `16px` | 日报正文 |

> **`--accent` 是红色（`#d32029`），这是该设计系统的身份色，不是错误色。**
> 页面上"错误"必须使用 `--danger`（`#cf1322`），不得与 `--accent` 混用 ——
> 该设计系统同时包含 `--accent` 与 `--danger` 两个令牌，混用会让"品牌重音"与"故障告警"
> 在视觉上无法区分。
>
> **来源口径（已实测核对，见 ADR-004）**：同一设计系统目录下的 `DESIGN.md` 文本
> 给出的是通用占位色（`#1677FF` 等）并与令牌文件冲突；以**机器可读的
> `design-tokens.json`（`layer: A1-identity`，`sourceBackedTokens: 56`）为准**。
