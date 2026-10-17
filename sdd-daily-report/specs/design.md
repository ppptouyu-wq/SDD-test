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
  message: str           # 提交信息（Commit Message，**完整原文**：标题 + 正文；
                         # 日报只呈现标题与正文摘要，见 §3.3）
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
| status      | str               | 正常 / 迟到 / 早退 / 缺勤 / 休假 / 签到缺失 / 签退缺失 |
```

字段级契约与状态取值见 `contracts/data-models.md`；本节的 `AttendanceRecord` 与
`MemberReport.attendance` 是 v1.1 迭代在架构层新增的部分。

#### 3.0.1 考勤接口的真实响应结构与字段映射（v1.8.4 缺陷修复时补）

v1.1 的解析代码按"猜想的响应结构"写成，读的键 `data.user_tasks` 与字段
`check_date` / `check_in_time` / `check_out_time` / `status` **在真实响应里都不存在**
（真实键是 `data.user_task_results`）。因为读不到就"成功地返回 0 条"，
日报一路渲染成「今日无记录」——**明明是"没解析到"，却长得像"没打卡"**。
真实结构（2026-10-13 实测 `POST /attendance/v1/user_tasks/query`）如下：

```jsonc
{"code": 0, "data": {"user_task_results": [{
  "user_id": "emp_0001",        // 即 employee_id（不是 open_id）
  "day": 20261013,              // 整数 YYYYMMDD，不是 "2026-10-13"
  "employee_name": "…", "group_id": "…", "shift_id": "8",
  "records": [{                 // 一天可能有多段班次 → 数组
    "check_in_result": "Lack",  // 签到结果：Normal / Late / Lack / …
    "check_out_result": "Normal",
    "check_in_record":  { "check_time": "1791894294", … },  // 签到缺失时**该键不存在**
    "check_out_record": { "check_time": "1791894294", … },
    "check_in_shift_time": "1791853200",   // 班次应到/应走时间
    "check_out_shift_time": "1791885600"
  }]}]}}
```

- `check_time` / `*_shift_time` 是**纪元秒的数字字符串**（消息接口的 `create_time`
  是毫秒 —— 两处量级不同，解析器必须按量级判断）。
- 一天多段班次：取当日**最早**签到与**最晚**签退合成一条 `AttendanceRecord`。
- 状态判定顺序（自上而下，先命中先返回）：

| 条件 | status |
|---|---|
| 既无签到也无签退 | 缺勤 |
| 无签到、有签退 | **签到缺失**（v1.8.4 新增：真实数据里 `check_in_result == "Lack"` 且 `check_in_record` 键缺失） |
| 有签到、无签退 | 签退缺失 |
| 两者都有，任一段签到结果为 `Late` | 迟到 |
| 两者都有，任一段签退结果为 `Early` | 早退 |
| 其余 | 正常 |

- **解析不到时必须出声**：若响应里既没有 `user_task_results`、也没有旧键
  `user_tasks`，采集器要打 WARNING 并带上实际出现的键名，不允许再静默返回 0 条
  （v1.8.4 的教训就是"0 条"看起来完全合法）。
- **呈现时缺哪一侧就说哪一侧**：考勤行渲染成「（签到 - 签退）」，
  缺失的一侧写"无签到"/"无签退"，不得借用状态名（v1.8.4 前把 `None`
  一律写成"签退缺失"，于是"缺签到"会被渲染成「签退缺失 - 20:24」，左右颠倒）。

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

### 3.3 提交信息在日报里的呈现（v1.5 需求变更新增）

**触发**：`CommitRecord.message` 装的是 GitHub 返回的**完整**提交信息。本项目自己的提交
（按书图 7-8 闭环写的规范链说明）正文有几十行，原样贴进日报后，「代码提交」段被撑成一大坨，
收件人根本读不下去。

**规则**（渲染期行为，`generator/template.py` 的 `summarize_commit()`）：

1. **标题** = 提交信息的第一行（去掉首尾空白），原样保留。空标题按空串处理，不造占位符。
2. **摘要** = 正文（第一行之后的部分）取**第一个非空连续段落**，段落内换行按 Markdown 软换行
   的惯例用单个空格接续，然后从中截取**前 3 句**。
3. **切句**：以中英文句末标点 `。！？!?` 为界（标点跟随前一句），英文句点 `.` 只在后跟空白时才算句末
   （避免把 `main.py`、`v1.2`、`99992402` 这类切碎）。
4. **长度上限 200 字符**：超过则按字符截断并追加 `…`。这道兜底是防止"一整段没有句号"的提交把邮件撑爆。
5. **没有正文**（例如 GitHub 网页端上传文件产生的 `Add files via upload`）时，摘要为空串，
   日报只显示标题——不渲染空占位、也不把上一条提交的内容串下来。
6. **摘要不落库**：`CommitRecord.message` 与数据库 `payload` 都保留完整原文，截断只发生在渲染期，
   将来换呈现方式不必重新采集。
7. Markdown 里摘要渲染成标题下方**缩进的子列表项**（`  - …`）。因此 `markdown_to_html()`
   必须把缩进项渲染成**嵌在父 `<li>` 内部的嵌套 `<li>`**——既不能退化成 `<p>`，也不能
   拍平成与提交标题同级的项（拍平后收件人分不清哪句摘要属于哪条提交，这正是 v1.5.1 修掉的呈现缺陷）。
8. **相邻提交之间必须一眼可分**：顶层条目之间要有明显的间距，摘要相对标题更弱（缩进 + 弱化字色），
   使"标题—摘要"在视觉上各自成组。间距由邮件 HTML 的样式负责，不靠往 Markdown 里插空行
   （插空行会让列表变成 loose list，摘要项反而被 `<p>` 包起来）。

> 对应验收标准：`proposal.md` §3.1「每条记录只呈现提交标题 + 正文摘要」「正文为空时只显示标题」；
> `tasks.md` Task 6 的 v1.5 段落。
>
> **v1.6 起邮件正文不再走 Markdown 转换**：邮件改由 `render_email_html()` 直接按结构渲染
> （§6.7），标题与摘要的拆分仍复用 `summarize_commit()`。第 7/8 条对
> `markdown_to_html()` 继续有效 —— 它保留为"把已存档 Markdown 转成 HTML"的通用转换器
> （例如补发历史日报），其"嵌套而非拍平"的要求不撤销。

### 3.4 工程进展数据模型（v1.8 需求变更新增）

**触发**：飞书任务的采集口径被实测证伪（详见 `docs/飞书接入诊断报告.md` 的修订说明：
`/task/v2/tasks` 只认 `user_access_token`、只列"我负责的"任务，`project_id` 与时间窗参数**从未生效**），
"任务进展"格在真实租户里只能长期为空。与此同时，**这个项目自己的迭代过程**——一天内
v1.2→v1.8、规范与代码进同一个提交、验收标准覆盖率 97/98——才是最有说服力的工作证据，
却完全没有进入日报。v1.8 把这部分补成一个**团队级**板块「今日工程进展」。

**取数原则：只用两种可复现的客观事实**

1. 本地 Git 历史（`git log` / `git show --stat`）；
2. 规范覆盖率的**静态统计**（`sdd_agents.metrics.map_acceptance_to_tests()`）。

**不跑测试、不做估算、不调模型**。日报里出现的每个数字都要能被同一条命令复现——
这是"过程证据"与"自吹"的分界线。

```python
@dataclass
class RepoProgress:
    """当日工程进展（v1.8 新增；团队级，不进 MemberReport）"""

    commit_count: int          # 当日提交数
    version: str               # 当日最高迭代版本号，如 "v1.7"；无则空串
    change_layer: str          # 变更起点层：需求变更 / 设计变更 / 任务变更 / 缺陷修复 / 未识别
    spec_changed: list[str]    # 当日被改动的规范文件（仓库根相对路径）
    code_changed: bool         # 当日是否改动了代码或测试
    same_commit: bool          # 是否至少有一个提交同时改了规范与代码（书图 7-8 的闭环要求）
    spec_coverage: str         # 规范覆盖率文本，如 "97/98 = 99.0%"；取不到则空串
    last_commit_date: str      # 当日**没有**提交时填"最近一次提交的日期"（YYYY-MM-DD）；v1.10
```

**`last_commit_date` 的取数与口径（v1.10）**：当日 `commit_count == 0` 时，再跑一次
`git log -1 --until=<当日 00:00> --pretty=format:%ad --date=short`（v1.10 的实现口径），
把"最近一次提交"的日期填进去。

- 当日有提交时该字段为空串（"最近一次"就是今天，没有增量信息）；
- 仓库里根本没有历史、或命令失败时为空串（**不允许**退回"当前时间"之类的臆测值）；
- 目的：让「今天没动」与「已经很久没动」在日报上能区分开。只写 `当日 0 次提交` 时，
  读者无法判断这是"周末没干活"还是"项目停了三周"。

**变更起点判定表**（把书里"找到受影响的最上游层级，从那里开始修改"变成可执行判定）：

| 当日改动的最高层 | 判定 | 起点 |
|---|---|---|
| `specs/proposal.md` | 需求变更 | proposal |
| `specs/design.md` 或 `specs/contracts/**` | 设计变更 | design |
| `specs/tasks.md` | 任务变更 | tasks |
| 只有代码与测试 | 缺陷修复 | 代码 |

多层同时改动时取**最上游**那一层（表自上而下即优先级）。判定**只看路径，不看提交信息**——
提交信息怎么写是人的自律，而改了哪个文件是事实。

路径要先按**项目根归一**再比对：仓库把项目放在子目录里时（本仓库就是
`sdd-daily-report/specs/proposal.md`），截到 `specs/` 这一段再判定。否则判定表在嵌套布局下
整体失效 —— 需求变更会被误判成"缺陷修复"（v1.8 真跑时踩到，同一个提交里补的这条说明）。

**它不是数据源**：`RepoProgress` 不写入 `DailyReport.sources`（见 `proposal.md` §4「工程数据源」）。
理由：它不是外部协作平台，失败原因绝大多数是"这台机器上不是 Git 仓库"——那种情况属于
**可解释的缺失**，不该把整份日报打成 `partial`，也不该占用三态/四态语义。取不到时
`progress=None`，板块渲染中性空态「今日未记录工程进展」，同时写一条 WARNING 日志。

> 对应验收标准：`proposal.md` §3.1 的 v1.8 四条与 v1.10 三条；`tasks.md` Task 13。

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

# lark_msg.collect 的过滤口径（v1.8.3 明确第 2 条）
#   1. 跳过系统消息：入群提示、群名变更等 msg_type='system'，其 sender.id 为空串
#   2. **排除应用自身发送的消息**：sender.sender_type == "app"，或 sender.id 就是本应用的
#      app_id（cli_ 开头）。理由：机器人推到群里的日报会命中「项目」「需求」等关键词，
#      但它不是"协作沟通"；而且它的 sender 不在成员身份映射表里，归属匹配只能**碰巧**
#      把它丢掉 —— 那是"运气"，不是规则。显式排除后，即使将来成员表里出现应用 ID，
#      机器人自己的推送也不会变成某个成员的"协作沟通"（proposal §3.1 v1.8.3）
#   3. 关键词过滤：任一关键词命中即保留（keywords 为空则全收）
#   4. 敏感词过滤：命中即剔除

# 采集层接口（v1.1 新增）
lark_attendance.collect(
    since: date,               # 起始日期
    until: date                # 结束日期
) -> CollectResult[AttendanceRecord]   # 返回考勤记录（含 success 标志位）

# 采集层接口（v1.8 新增；本机 Git 仓库，走"工程数据源"语义，不进 sources）
repo_progress.collect(
    repo_path: str | Path,         # 仓库路径（配置 report.repo_path；空串则由项目目录向上探测）
    since: datetime,               # 起始时间
    until: datetime,               # 结束时间
    *,
    coverage: Callable[[], str] | None = None  # 规范覆盖率取数（注入；None 表示未记录）
) -> CollectResult[RepoProgress]   # 成功时 records 恰好一条；取不到仓库信息时 success=False

# 生成层接口
generator.generate(
    members: list[MemberReport],   # 各成员数据
    date: date,                    # 日报日期
    team_name: str,                # 团队名称
    sources: dict[str, bool] | None = None,      # 各数据源当日是否成功（v1.2）
    progress: RepoProgress | None = None,        # 当日工程进展（v1.8，见 §3.4；None = 未记录）
    title: str = ""                # 邮件标题区的标题（v1.11）；空串 = 回退到「报告名 + 日期」
) -> DailyReport                   # 返回日报

template.summarize_commit(         # v1.5 新增：提交信息的呈现拆分（见 §3.3）
    message: str,                  # 完整提交信息（标题 + 正文）
    *,
    max_sentences: int = 3,        # 正文摘要最多保留几句
    max_chars: int = 200           # 摘要字符上限，超出截断并补 "…"
) -> tuple[str, str]               # (标题, 摘要；没有正文时摘要为空串)

template.render_email_html(        # v1.6 新增：邮件日报的版式渲染（见 §6.7）
    report: DailyReport,           # 日报对象；计数与总结句由它的真实数据汇总
    *,
    title: str = ""                # v1.11：与邮件主题同源的标题；空串 = 回退到 v1.7 的形态
) -> str                           # 完整 HTML 文档（内联样式为主 + <style> 渐进增强）

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

# 编排层接口（v1.4 变更）
main.run(
    config: AppConfig,
    *, day: date | None = None,        # 日报日期，缺省取当天
    mock: bool = False,                # 使用内置演示数据
    push: bool = True,                 # 是否真实推送
    tasks_file: str | None = None,     # 手动任务数据文件
    working_days_only: bool = False    # 仅工作日运行；默认 False = 每天都能跑
) -> dict                              # 执行摘要
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

### 6.6 触发时机与运行日期（v1.4 需求变更新增）

**(1) 默认每个自然日都能跑**：日报生成不设日期门槛。周末与配置的法定节假日
同样会采集、生成、推送。原因是"今天该不该发日报"是**调度策略**，
不是**产品能力** —— 把调度策略写死进产品，会让补跑、演练、值班日报
（例如周末线上值班也需要日报）全部无法进行。

**(2) 仅工作日模式是显式选项**：`main.py --working-days-only` 才启用
`shared/calendar.is_working_day()` 判断，命中非工作日时抛
`NonWorkingDayError`，由 `main()` 捕获、记录日志、打印跳过原因并返回 0
（跳过不是失败）。

**(3) `holidays` 配置的含义随之收窄**：`report.holidays` 只在"仅工作日模式"下
生效；默认模式下填了也不会跳过。这一点必须写进配置模板的注释，
否则使用者会以为"填了节假日就一定会跳过"。

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

### 6.7 邮件日报的版式与配色（v1.6 需求变更新增；v1.7 换色与结构微调；v1.9 概览下增一行代码变更行数；v1.10 空态分板块说清）

**(1) 邮件是独立画布，不与展示层共用色板**：`webview/` 用的是 `ant` 设计令牌（§6.5、§6.6(4)），
而邮件会被转发到任意邮件客户端、主要在手机上阅读，两者不是同一块画布。因此邮件单独定义一套
"阅读型"色板（下表），不引用 `ant` 令牌。特别注意 **`--accent`（红 `#d32029`）不用于邮件** ——
大面积红色在邮件里传达"告警"意味，而日报是日常汇报；红色只保留给"采集失败"。

> **v1.7 换色的理由**：v1.6 的色板是照需求参考稿（一张手机邮件截图）取的蓝 `#1a6cf5`，
> 上身后一眼就是"模板色"，与参考稿几乎无法区分。v1.7 改用**青碧（teal）**作身份色，
> 同时补两处结构特征（顶部通栏、成员名条），让版式有自己的识别度。参考稿的版式骨架
> （大标题 / 概览计数卡 / 总结块 / 按成员分组）保留不变。

| 用途 | 取值 | 说明 |
|---|---|---|
| 品牌色（标题文字、概览数字、色边） | `#0f766e` | 青碧，邮件身份色 |
| 身份深色（顶部通栏、成员名条底色） | `#0b5b55` | 比主色深一档 |
| 信息块 / 总结块 / 计数卡底色 | `#e7f4f1` | 浅青提示块 |
| 中性块底色（"今日无记录"） | `#f5f6f8` | 不参与告警色阶 |
| 卡片边框 / 分隔线 | `#e3e7ea` | 统计卡与章节标题下的细线 |
| 主文本 / 次级文本 | `#1d2129` / `#5b6472` | 正文与说明、元信息 |
| 页脚说明文字 | `#9aa1ab` | 页面最底部的一行小字 |
| 页面底色 / 内容卡 | `#eef1f3` / `#ffffff` | 外层底、白色内容卡 |
| 告警（数据获取失败） | `#cf1322` 文字 + `#fdecec` 底 | 沿用 §6.1 的失败语义 |

**硬约束：邮件 HTML 里出现的颜色必须落在这张表内。** 实现侧由 `template.EMAIL_PALETTE`
（白名单集合）与单测 `test_email_html_only_uses_the_design_palette` 守住 —— 随手加一个
色板外的颜色，版式就会慢慢跑偏成"谁都能改一点"的样子。

**(2) 版式结构**（五段，从 `DailyReport` 直接渲染；v1.7 把原"标题区"与"信息块"合并为一段，
v1.8 在"今日总结"之后插入团队级的"今日工程进展"）：

1. **标题区**：整宽品牌深色通栏（4px）+ 报告名与日期同行（报告名取自 `team_name`，
   **不再拼接"工作日报"后缀**：部署方给的本来就是完整名称，例如「孔子博智能日报」，
   再拼后缀会变成「孔子博智能日报工作日报」）+ 生成时间右对齐 + 一条细分隔线；
   **v1.11：标题区显示的名字与邮件主题同源** —— `render_email_html()` 接收
   `title`（由 `report_title()` 用同一份 `email` 配置渲染，也就是主题那一串）；
   传入时标题区**直接呈现该标题**，不再另外渲染日期片段（标题里已经含日期，
   再挂一次就会变成「研一孔子博2026-10-15智能日报　2026-10-15」）；不传时保持
   v1.7 的形态不变。主题与正文标题因此不可能各写一套、各说各话；
2. **今日概览**：三张计数卡（代码提交 / 任务更新 / 协作讨论），浅青底 + 顶部品牌色条，
   数字用大号深青；卡片下方紧跟**一行当日代码变更行数汇总**（v1.9）：
   `当日代码变更 +N / -M 行`，其中 N/M 由当日**全部**提交的 `additions`/`deletions`
   求和得到（跨成员、跨仓库合计）；当日没有提交时显示 `+0 / -0 行`，不隐藏、不留白。
   这一行补的是"量"：计数卡回答"提交了几次"，它回答"改了多少行"。
   该行**只出现在邮件渠道**，与「今日工程进展」板块同理 —— 邮件是每天给收件人看的成品，
   展示页保持 v1.2 定下的"总览 + 成员四段"不变（§6.5、§6.8(1)）；
3. **今日总结**：浅青提示块，一句话概括当日量与活跃度；
4. **今日工程进展**（v1.8，团队级，见 §6.8）：来自本机 Git 仓库的客观事实，
   取不到时渲染中性空态「今日未记录工程进展」；
5. **按成员分组**：每人一条浅青"成员名条"（实底条，与白底细线的章节标题刻意区分），
   依次是代码提交（per-repo 汇总句 + 主要变更列表）、任务进展、考勤、协作沟通。

**邮件主题**由 `report_title(team_name, day)` 按**配置模板**给出（v1.8 起可配）：

- `email.subject_pattern`，默认 `"{team_name} {date}"`（例如「孔子博智能日报 2026-10-11」）；
  主题属于邮件层，因此配置项落在 `config.yaml` 的 `email` 段而不是 `report` 段；
- 可用占位符：`{team_name}`（报告名）、`{date}`（`YYYY-MM-DD`）、`{grade}`、`{owner}`
  ——后两个来自配置，用于「研一孔子博2026-10-15智能日报」这类要求（占位符之间的分隔符由模板自己写，
  可以是空格、`+`，也可以直接连起来；本项目当前用的是 `"{grade}{owner}{date}智能日报"` 直连形式，
  见 `config.yaml` 的 `email` 段）；
- **日期必须保留**：一天一封，收件箱里要能分辨是哪天；
- 年级、姓名这类**个人属性一律走配置**，不写进模板代码（换个使用者只需改 `config.yaml`）。

**(3) 计数与总结句一律由真实数据现算**：`render_email_html()` 只做汇总（求和、连接仓库名），
不调用任何模型，也不生成未采集的指标。这对应 `proposal.md` §2.2 对"AI 摘要"的排除项 ——
"今日总结"是**确定性模板句**（例如「共完成 3 次代码提交，1 项任务有更新，整体进展平稳。」），
不是 AI 生成内容。

**(4) 邮件客户端兼容性**：卡片与配色用 `<table role="presentation">` + **内联样式**表达；
`<style>` 块只作渐进增强（字体栈、圆角、移动端断点）；**不引用任何外部资源**
（CSS 里不得出现 `url(`），不依赖 JavaScript，不使用 Web 字体。原因是邮件客户端会剥掉
`<style>`、屏蔽外链图片，内联样式 + 表格布局的存活率最高。

**(5) 空态与失败态**：成员某板块当日为空时**分板块给出具体说明，不留白**（v1.10）。
统一写一句「今日无记录」的问题是：读者看不出到底是"没有代码提交"还是"没接到数据"，
四格长得一模一样，一份空日报等于什么都没说。因此每格说清"空的是什么"：

| 板块 | 空态文案 | 说明 |
|---|---|---|
| 代码提交 | 当日无代码提交 | 当天没有任何提交记录 |
| 任务进展 | 当日无任务状态变更 | 当天没有任务状态流转 |
| 协作沟通 | 当日无符合条件的讨论 | 当天群内没有命中关键词的**真人**发言（v1.8.3） |
| 考勤 | 当日无打卡记录 | **采集成功**但本人当天没有打卡记录 |
| 工程进展 | 今日未记录工程进展 | 本机取不到仓库信息（§6.8(4)）；当日无提交时另附最近一次提交日期（v1.10） |

考勤另沿用 v1.1/v1.3 的三态口径：有记录 → 正常渲染；采集失败 → 「考勤数据暂不可用」；
采集成功但本人无记录 → 「当日无打卡记录」。注意**展示页口径不变**（`webview/views.py` 的
`TEXT_NO_RECORD_TODAY = "今日无记录"`，v1.2 定下的展示层文案），本节只约束日报本体
（Markdown 与邮件）。

采集失败的来源在顶部用告警块显式列出，严禁静默跳过（§6.1）。

### 6.8 今日工程进展板块的取数规则（v1.8 需求变更新增）

**为什么放在邮件里而不是展示页**：展示页（v1.2）的验收标准是"总览 + 每位成员的四段"，
面向的是**某一天日报的回溯阅读**；而"工程进展"是**过程证据**，它的读者是收件箱里的人。
因此 v1.8 **只在邮件渠道增加这一段**，展示页保持 v1.2 的约定不变——
`RepoProgress` 会随 `payload` 一起落库，将来展示页要用，加视图字段即可，无需重新采集。

**(1) 板块内容**（团队级，位于"今日总结"之后、"按成员分组"之前）：

| 行 | 内容 | 取数 |
|---|---|---|
| 迭代 | 当日最高版本号 + 变更起点层 | 提交标题里的 `v1.8` 这类版本号 + §3.4 的路径判定 |
| 变更起点 | 「起点在 design.md（设计变更）」这类一句话 | §3.4 判定表 |
| 规范链 | 当日被改动的 `specs/**` 文件列表 | `git show --stat` 的文件清单 |
| 闭环 | 「规范与代码在同一个提交」✓ / ✗ | 是否存在同时改动 `specs/` 与代码的提交 |
| 规范覆盖率 | 「验收标准被测试覆盖 97/98 = 99.0%」 | `sdd_agents.metrics.map_acceptance_to_tests()` |
| 最近一次提交（v1.10，仅当日 0 次提交时出现） | 「最近一次提交：2026-10-13」 | `git log -1 --until=<当日 00:00> --pretty=format:%ad --date=short`（§3.4） |

**(2) 取数实现**：`collector/repo_progress.py` 只用标准库 `subprocess` 调用本机 git
（`git rev-parse --show-toplevel` 定位仓库根、`git log --since --until --pretty` + `--name-only`
取提交与文件清单；当日无提交时再补一次 `git log -1 --until=<当日 00:00> --date=short` 取最近一次提交日期，v1.10），
**不引入任何新依赖**，与采集层其他模块同样返回 `CollectResult`。
仓库路径默认由项目目录向上探测（零配置）；`report.repo_path` 可显式覆盖。
规范覆盖率通过**注入的 callable** 取（默认不注入 → 该项记为未记录），
避免采集层反向依赖 `sdd_agents`（那是本项目的工具，不是运行时依赖）。

**(3) 诚实性约束**：板块里不得出现"测试全部通过""代码质量优秀"这类**无法由同一条命令复现**
的断言。允许出现的只有 `git` 与 `metrics` 能直接算出来的数字。`proposal.md` §2.2 排除的是
"AI 摘要"——本板块是**确定性模板渲染**，没有模型参与。

**(4) 空态**：`progress is None` → 中性块「今日未记录工程进展」+ WARNING 日志，
不写成"采集失败"（本机没有 Git 仓库不是故障），也不参与 `sources` 四态（§3.4）。

## 7. 构建与交付流水线（v1.12 需求变更新增）

**触发**：课程主题转为 CI/CD，要求"提交后自动测试、通过后打包镜像、推送到镜像仓库"。
本节把这个能力写成可执行的阶段划分与边界，避免流水线长成"谁都能往里塞一步"的样子。

### 7.1 阶段划分与触发条件

| 阶段 | 触发条件 | 执行内容 | 产出 | 失败后果 |
|---|---|---|---|---|
| ① 测试（CI） | 每次 `push`、每个 `pull_request` | 三个项目各自独立跑 pytest（矩阵）；规范治理四件事：多工具配置同步、`sdd_agents review`、`sdd_agents team`、`sdd_agents guard`/`metrics` | 红/绿结论 + `junit` 报告（artifact） | **流水线终止**，不进入构建 |
| ② 构建 | 仅 `push` 到 `main` 或打 `v*` 标签，且 ① 为绿 | `docker build`（多阶段，见 §7.2） | 本地镜像 + 构建日志 | 终止，不推送 |
| ③ 发布 | 仅打 `v*` 标签，且 ② 成功 | `docker login ghcr.io` → `docker push`，打三种标签（§7.4） | GHCR 中的镜像 + digest | 终止，并在日志里区分"登录失败"与"推送被拒" |
| ④ 部署 | **本期不做**（`proposal.md` §2.2） | — | — | — |

**为什么测试与构建必须分段而不是一个 job**：一个 job 里跑完所有事，测试红了照样已经把镜像
构出来并推上去了。分成两个 job 并让 ② 显式 `needs: ①`，才能让"测试不过就不产出制品"这条
验收标准（`proposal.md` §3.1 v1.12）成为**机制**而不是纪律。

**为什么不需要 ⑤ 部署**：镜像推到仓库后，它已经是"随时可部署"的制品 —— 这正是
Continuous **Delivery** 的终点。是否自动上生产（Continuous Deployment）取决于有没有目标机器，
本项目没有，因此明确排除，不写成"以后再说"的含糊话。

### 7.2 镜像结构与 `.dockerignore` 口径

镜像采用**多阶段构建**：builder 阶段装依赖到独立目录，runtime 阶段只拷贝
"依赖 + 代码 + 规范"，不带编译器缓存与构建痕迹。

| 进镜像 | 不进镜像（`.dockerignore`） |
|---|---|
| `shared/`、`collector/`、`generator/`、`notifier/`、`main.py` | `.git/`（见 §7.3 的降级口径）、`.github/` |
| `specs/`（护栏运行期需要读，见 `Dockerfile` 注释） | `tests/`、`_build_scripts/`、`_shots/`、`pages/` |
| `scripts/`、`CLAUDE.md`、`.claude/` | `data/`（本地 SQLite 与日报历史）、`.env` |
| `config.yaml.example`（模板，不含真实值） | `config*.yaml`（真实配置由部署方挂载） |

- **非 root 运行**：镜像内创建普通用户并以它启动，避免容器内进程以 uid 0 运行。
- **`HEALTHCHECK`**：做镜像自检 —— 导入采集/生成/推送/共享四层模块与编排入口
  （`python -c "import main, shared.models, collector.github, generator.template, notifier.email"`），
  容器状态可被 `docker ps` 观测。刻意**不依赖外部配置**：镜像里没有 `config.yaml`，
  而 `main.py --check` 需要挂载配置后才成立，用它做健康检查会在"尚未挂载配置"的部署阶段
  永远报 unhealthy，把真实故障和"还没配好"混为一谈。
- **首次部署先自检**：`docker run --rm … <image> --check`（此时配置已挂载），比健康检查更严。
- **不常驻**：`ENTRYPOINT ["python", "main.py"]`，一次执行即退出（`proposal.md` §2.3 的
  "基于 Cron 的定时任务，不需要常驻后台服务"），因此**不使用** `webview --serve`。

### 7.3 容器内的"工程进展"降级（重要边界）

`collector/repo_progress.py` 依赖**本机 Git 历史**（§3.4）。镜像里刻意**不包含** `.git`
（一个仓库的历史动辄几十 MB，且会把提交者邮箱等信息带进制品），因此容器内跑日报时：

- `find_repo_root()` / `git log` 失败 → `progress=None` → 邮件渲染中性空态
  「今日未记录工程进展」，**不报错、不影响其他板块与推送**（`proposal.md` §3.3 v1.12）；
- 想要容器里也有这一板块，唯一被允许的做法是部署时把 `.git` **只读挂载**进去
  （`-v $(pwd)/.git:/app/.git:ro`），本项目不为它增加任何配置项或网络取数逻辑 ——
  那会把"工程数据源"变成外部依赖，破坏 §3.4 "它不是数据源"的判定。

### 7.4 可追溯性与回滚

- 镜像标签：`sha-<commit 前 7 位>`（不可变，用于回滚）、`vX.Y.Z`（版本语义）、`latest`（便于手拉）；
- 每次推送输出 `digest`（`sha256:…`），部署时应锁定 digest；
- **回滚 = 用上一个 `sha-*` 重新拉起**，不需要重新构建。

### 7.5 凭据边界

- 流水线里**只用** `GITHUB_TOKEN`（工作流自动注入）推送镜像，权限收敛到
  `packages: write` + `contents: read`；不引入任何长期 PAT。
- 真实运行凭据（`GITHUB_TOKEN` 采集用、`LARK_APP_ID/SECRET`、`SMTP_PASSWORD`）一律走
  Secrets 或部署侧的 `--env-file`，**不进镜像、不进仓库、不 `echo`**。
- CI 中的端到端验证只跑时不连接任何外部服务的演示模式，不向真实群或邮箱发信
  （`proposal.md` §3.1 v1.12）。

### 7.6 与展示层的关系

展示层（v1.2）只监听 `127.0.0.1`（§6.5），**不进入容器**：容器里跑 `--serve` 对外没有意义，
放开监听又会违反 §6.5 的硬约束。展示页仍是"本机按需启动、用完即关"的工具。


