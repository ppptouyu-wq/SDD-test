# 智能日报生成器（sdd-daily-report）

> 《SDD 实战：规范驱动开发之道》主项目复现。
> 按书中的 **规范驱动开发（SDD）** 方法论完成：先写 `proposal.md` → `design.md` → `tasks.md`，再由代码实现并逐条验收。

## ⚠️ 两件必须知道的事

**① 运行结果输出去哪：`data/reports.db`（SQLite），不是终端、也不生成文件。**

| | |
|---|---|
| 结果在哪 | `data/reports.db`，表 `daily_reports`，一天一条（`report_date` 主键，重跑覆盖） |
| 存了什么 | 每条记录含 `markdown`（Markdown 日报）、`html`（邮件正文）、`payload`（结构化 JSON）三份 |
| 什么时候写 | 采集 → 聚合 → 生成 → **写库（`main.py:151`）→ 之后才推送**。推送失败照写 |
| 什么时候不写 | ① 加了 `--working-days-only` 且当天是周末/节假日（v1.4 起默认**不**跳过，双休日节假日照常生成）；② 所有数据源都失败（不生成空日报）；③ `--check` 模式 |
| 终端那几行是什么 | **日志**（JSON 走 stderr），不是结果 |
| 会生成 md/html 文件吗 | **不会**。`--push` 时邮件进对方邮箱、消息进飞书群，本地不留副本 |
| 会上传吗 | `data/` 被 `.gitignore` 忽略，**不会** |

**② 本仓库不含任何凭据，想让邮件/飞书发出去必须自己配。**

仓库里只有占位模板：`LARK_APP_ID=`（空）、`SMTP_PASSWORD=`（空）、
`oc_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`、`your-account@qq.com`。
真实配置文件（`config.yaml`、`config.github.yaml`、`config.email.yaml`、`.env`）
全被 `.gitignore` 排除，密钥一律从环境变量读，**从未进入任何提交**。

**推送不是默认行为**（`main.py:443`：`push = (not args.mock) if args.push is None else args.push`）：

| 命令 | 会不会发 |
|---|---|
| `python main.py --date 2026-08-20` | ✅ 会发（正式运行默认推送） |
| `python main.py --date 2026-08-20 --mock` | ❌ 不发（mock 演练默认不推送，防误发） |
| 加 `--push` / `--no-push` | 显式决定，覆盖上面的默认 |

配什么、缺了会报什么错，见下面「[接入真实数据源](#接入真实数据源)」与
「[发真实邮件（QQ 邮箱）](#发真实邮件qq-邮箱)」两节。

## 项目做什么

从团队现有协作平台抓取当日工作数据，自动生成结构化日报并推送给相关干系人。

- **数据采集**：GitHub 当日 Commit、飞书任务状态流转、飞书群消息（按关键词过滤 + 敏感词剔除）、飞书考勤
- **日报生成**：按"代码提交 → 任务进展 → 协作沟通"三段式编排，每人独立段落，输出 Markdown + HTML
  （v1.15 起**邮件渠道**再收敛一层：只留「代码提交 → 任务进展 → 考勤」，协作沟通与提交明细留在 Markdown 与展示页）
- **推送通知**：SMTP 发送 HTML 日报；飞书机器人推送 Markdown 到群

## 快速开始

### 方式一：VS Code（推荐，图形化一键运行）

1. **打开正确的文件夹**：用 VS Code 打开 `sdd-daily-report` 这一层（**不要**打开它的上级目录）。
   代码里用的是 `import shared` 这种绝对导入，工作区根目录必须是这里。
2. **装 Python 扩展**：扩展面板搜 `Python`（Microsoft 官方，ID `ms-python.python`）。
   一键运行依赖它自带的 `debugpy`，没装的话"运行和调试"里看不到下面这些配置。
3. **选解释器**：`Ctrl+Shift+P` → 输入 `Python: Select Interpreter` → 选 Python 3.11+ 那个
   （你这台是 `D:\APP\Anaconda\python.exe`，3.12.7）。
4. **按 F5 运行**：`Ctrl+Shift+D` 打开"运行和调试"，左上角下拉框里已预置 7 个配置：

   | 配置 | 作用 |
   |---|---|
   | ① 演练：mock 数据（无需凭据） | **先跑这个**，不需要任何 token |
   | ② 真实 GitHub 仓库（2026-04-24，不推送） | 拉真实提交 |
   | ③ 健康检查（--check） | 逐项检查配置 |
   | ④ 今天（真实推送，慎用） | 会真的发邮件/推飞书 |
   | ⑤ 跑全部测试（pytest） | 481 个用例 |
   | ⑥ 只跑当前打开的测试文件 | 调试单个用例 |
   | ⑦ 展示页：本地只读查看已生成的日报 | 起本地网页看日报（v1.2 新增） |

> 配置②③④ 用的是 `config.github.yaml`。仓库里**只分发脱敏模板**
> `config.github.yaml.example`，首次使用请复制：
> `cp config.github.yaml.example config.github.yaml` 后填入自己的值。
> 真实配置被 `.gitignore` 忽略（含环境相关信息，不应上传）。

5. **看结果**：终端里打的是**日志**（JSON，中文不会乱码），**真正的日报写进了
   `data/reports.db`**。测试也可以点左侧烧瓶图标，在"测试"面板里逐个跑/调试。

   想看日报内容本身，有三种办法：起展示页（浏览器里看，推荐）、导出成文件、直接查库：

   ```bash
   python main.py --serve              # 浏览器打开 http://127.0.0.1:8000/
   python _show_report.py              # 导出 data/reports/<日期>.md 与 .html
   python -c "import sqlite3;print(sqlite3.connect('data/reports.db').execute('SELECT report_date,team_name FROM daily_reports').fetchall())"
   ```

### 方式二：命令行

```bash
# 1) 安装依赖
pip install -e ".[dev]"

# 2) 准备配置（成员映射表 + 各数据源参数）
cp config.yaml.example config.yaml

# 3) 端到端演示：无需任何外部凭据，用内置演示数据跑通全链路（生成 + 落库，不推送）
python main.py --config config.yaml --date 2026-08-20 --mock

# 4) 全量回归测试
python -m pytest

# 5) 健康检查（验证 GitHub / 飞书 / 邮件配置是否就绪）
python main.py --config config.yaml --check
```

> ⚠️ `main.py` 从任意目录都可以执行（默认配置与相对 `storage_path` 都锚在项目根，
> 见 `tests/test_main.py::test_project_paths_are_anchored_at_the_project_root_not_cwd`）；
> 但 `python -m pytest` 要在 `sdd-daily-report` 目录下执行，否则 `import shared` 会失败。

### 方式三：PyCharm

`File → Open` 选 `sdd-daily-report` → 配好解释器 → 右上角 `Edit Configurations` → `+` → `Python`：

- **Script path**: `<项目根>/main.py`
- **Parameters**: `--config config.yaml --date 2026-08-20 --mock`
- **Working directory**: **项目根目录**（必须）
- 环境变量：跑真实仓库时加 `GITHUB_TOKEN=ghp_xxx`

再在 `Settings → Tools → Python Integrated Tools` 里把测试运行器设为 **pytest**。

### 接入真实数据源

凭据放进环境变量（**不要写进配置文件**）：

```bash
export GITHUB_TOKEN=...        # 公有仓库可留空，但配额只有 60 次/小时（有 token 为 5000）
export LARK_APP_ID=...
export LARK_APP_SECRET=...
export SMTP_PASSWORD=...

python main.py --config config.yaml                  # 生成当天并推送
python main.py --config config.yaml --no-push        # 只生成不推送
python main.py --config config.yaml --date 2026-04-24 --push
```

VS Code 里把 `.env.example` 复制成 `.env` 填好即可，`launch.json` 的配置②③④会自动读取。

### 命令行参数

| 参数 | 作用 |
|---|---|
| `--config <路径>` | 配置文件，默认 `config.yaml` |
| `--date YYYY-MM-DD` | 日报日期，默认今天 |
| `--mock` | 用内置演示数据，不需要任何凭据（默认不推送） |
| `--tasks-file <路径>` | 从 JSON 文件读任务数据（飞书 API 不可用时的兜底，见下） |
| `--check` | 健康检查，不生成日报 |
| `--push` / `--no-push` | 强制推送 / 强制不推送 |
| `--working-days-only` | 仅工作日运行（v1.4）：周末或 `report.holidays` 命中的日期跳过生成并打印原因。**默认不开启**，即双休日、节假日照常生成并推送 |
| `--serve` | 启动本地只读展示页（v1.2），不采集、不生成、不推送 |
| `--port <n>` | 展示页端口，默认 8000（仅与 `--serve` 同用） |
| `--db <路径>` | 展示页读的日报库，默认取 `config.yaml` 的 `storage_path`，缺失时用 `data/reports.db`（仅与 `--serve` 同用） |

> `--serve` **故意放在加载配置之前**：展示页只读 `data/reports.db`，
> 不该因为本机没有 `config.yaml` 就起不来。库路径优先级：`--db` > `config.yaml` > `data/reports.db`。

### 飞书数据接不进来时怎么办

飞书的**个人待办**（`applink.feishu.cn/client/todo/detail?guid=...`）属于客户端私有接口，
开放平台 task API **读不到**它。而本项目 `lark_task.py` 用的是开放平台
`project_id` + `/task/v2/tasks`，两者不是同一体系。

如果手上只有个人待办链接、又想让任务进日报，用 `--tasks-file` 把内容写成 JSON：

```powershell
python main.py --config config.github.yaml --date 2026-04-24 `
  --tasks-file tasks.sample.json --no-push
```

`tasks.sample.json` 是格式示例。**数据路径与真实采集完全一致** ——
同样是 `CollectResult[TaskRecord]`，同样要过成员身份映射表，
所以不存在"另一套逻辑"，日报生成部分不需要任何特殊分支。

### 发真实邮件（QQ 邮箱）

`config.github.yaml` 已配好 `smtp.qq.com:465` 与收件人（在本地配置里填你自己的邮箱）。
只差授权码（**不是 QQ 登录密码**）：

```powershell
# QQ 邮箱 → 设置 → 账户 → POP3/IMAP/SMTP 服务 → 开启 SMTP → 生成授权码
$env:SMTP_PASSWORD="你的授权码"
python main.py --config config.github.yaml --date 2026-04-24    # 不带 --no-push 即真实发信
```

发信前可先自检（只握手、不登录、不打印凭据）：

```powershell
python check_email.py
```

### 常见问题

| 现象 | 原因 |
|---|---|
| `ModuleNotFoundError: No module named 'shared'` | `python -m pytest` 没在本目录下运行。直接跑 `main.py` 已不受工作目录影响（入口路径锚在项目根），但三个子项目有同名顶层包，pytest 仍要在各自目录里跑 |
| `…是非工作日，跳过日报生成` | 只会在显式加了 `--working-days-only` 时出现；去掉该参数即可在任意日期运行（v1.4，见 design.md §6.6） |
| 运行成功但 0 条记录 | 该日期的采集窗口内确实没有提交，换 `--date` |
| 飞书报"凭据缺失" | 正常：未配 `LARK_APP_ID/SECRET`，日报会标注"数据获取失败"，其余数据源照常 |
| 邮件报"发送失败" | 未设 `SMTP_PASSWORD`，或用的不是 SMTP 授权码（QQ 登录密码不可用） |
| 直接运行 `main.py` 不带参数 | 会跑"今天 + 真实推送"；v1.4 起周末与节假日同样会跑（想恢复旧行为加 `--working-days-only`） |

## 架构

管道式架构（`specs/design.md` § 1）：

```
GitHub API ─┐
飞书任务 API ─┼─▶ 采集层 collector ─▶ 生成层 generator ─▶ 推送层 notifier ─▶ 邮件 / 飞书群
飞书消息 API ─┘                          ▲                │
                                         │                ▼
                          共享基础层 shared（配置 / 日志 / 错误处理 / 存储）
                                         ▲
                                         │  （只读旁路，v1.2 新增）
                            展示层 webview ─▶ 浏览器 http://127.0.0.1:8000/
```

| 模块 | 职责 | 不负责 |
|---|---|---|
| `collector/` | 从外部 API 获取原始数据 | 不做格式化、去重、推送 |
| `generator/` | 组织数据生成 Markdown/HTML 日报 | 不做采集、API 调用、推送 |
| `notifier/` | 推送日报到邮件与飞书群 | 不做数据处理、日报生成、采集 |
| `shared/` | 配置、日志、错误处理、SQLite 存储 | — |
| `webview/` | 把库里的日报渲染成本地只读网页（v1.2） | 不做采集、生成、推送、写库 |
| `main.py` | 编排"采集→聚合→生成→推送" | — |

> `webview/` 是**只读旁路**：它从 `shared.storage` 读，不反向依赖任何业务模块，
> 因此 `main.py` 的 Cron 链路完全不关心它是否存在。

## 规范（第一手工件）

| 文件 | 回答的问题 |
|---|---|
| `specs/proposal.md` | 要解决什么问题（做什么 / 不做什么 / 怎么验收） |
| `specs/design.md` | 用什么结构解决（架构 / 模块职责 / 数据模型 / 接口契约 / ADR / 非功能约束） |
| `specs/tasks.md` | 按什么顺序交付（Task 1~12，每任务含输入/输出/依赖/验收标准） |
| `specs/contracts/data-models.md` | 数据模型字段级契约 |
| `specs/adrs/` | ADR-001 httpx / ADR-002 SQLite / ADR-003 CollectResult / ADR-004 展示层技术选型 |

## 任务进度

| # | 任务 | 状态 |
|---|---|---|
| 1 | 项目结构和基础配置 | ✅ |
| 2 | 共享基础层（config/logger/errors/storage） | ✅ |
| 3 | GitHub 采集模块 | ✅ |
| 4 | 飞书任务采集模块 | ✅ |
| 5 | 飞书消息采集模块 | ✅ |
| 6 | 日报生成模块 | ✅ |
| 7 | 邮件推送模块 | ✅ |
| 8 | 飞书推送模块 | ✅ |
| 9 | 主编排入口 | ✅ |
| 10 | 集成测试 | ✅ |
| 11 | 考勤采集模块（v1.1 新增） | ✅ |
| 12 | 本地只读展示层（v1.2 新增） | ✅ |
| 13 | 工程进展采集与邮件板块（v1.8 新增） | ✅ |

## v1.1 迭代（书中第 7 章 7.5）

按"需求变更 → 自上而下更新规范链 → 执行 → 全量回归"的流程完成了一次真实迭代：

1. **更新 `proposal.md`**：功能范围新增"采集飞书考勤数据"；验收标准新增"日报包含每人当日工时与出勤状态"。
2. **更新 `design.md`**：新增 `AttendanceRecord` 数据模型、`collect(since, until)` 接口契约；`MemberReport` 增加 `attendance` 字段。
3. **更新 `tasks.md`**：新增 Task 11；同步修改受影响的 Task 6（日报生成加工时统计）。
4. **逆向回溯**：实现中发现 `collect() -> list[Record]` 无法区分"无数据"与"采集失败"，违反 proposal.md §3.2"失败必须显式标注"。按 SDD 原则**回到规范层**修复，新增 **ADR-003** 将返回类型改为 `CollectResult{success, records, error_message}`，而不是在代码里打补丁。
5. **全量回归**：`python -m pytest` 全部通过。

## v1.2 迭代：本地只读展示页（书中第 7 章 7.4~7.5）

第二次真实需求变更，走的是书里图 7-4「需求变更的 SDD 流程：自上而下的涟漪」与
图 7-8「完整的 SDD 迭代循环」：

1. **判定变更类型**：引入一个页面上能看到日报的展示层 → 改变了"做什么"，
   属**需求变更**，起点是 `proposal.md`（不是代码）。
2. **更新 `proposal.md`**：§2.1 新增"本地 Web 展示页"；§2.2 排除项补上
   不做登录/权限、不做编辑与重新生成、不做搜索与图表、不做手机端；§3 新增验收标准。
3. **更新 `design.md`**：新增展示层视图模型、`status` 推导规则、
   `webview.list_reports/get_report/serve` 接口签名，以及 §6.5 三条硬约束。
4. **更新 `tasks.md`**：新增 Task 12（13 条验收标准），标注它不在关键路径上。
5. **写 ADR-004**：运行时零新增依赖（用标准库 `http.server`，不引 FastAPI/Flask），
   视觉采用已有设计系统的令牌。ADR 只追加、不修改。
6. **回溯更新规范**（图 7-8 的"规范缺陷 → 回溯更新规范"分支）：实现中发现
   老日报没有记录数据源成败，原文把"`sources` 为空"并入 `failed`，等于把
   **"没记录"说成"全部失败"**。没有在代码里绕过去，而是回到规范层拆出第四态
   `unknown`（页面文案"未记录数据源状态"），并记入 ADR-004 的补充记录。
7. **全量回归**：`python -m pytest` 全绿。

### 展示页怎么用

```bash
python main.py --serve                # 默认 http://127.0.0.1:8000/ ，读 data/reports.db
python main.py --serve --port 8010    # 换端口
python main.py --serve --db other.db  # 换日报库
```

三条约束由 `scripts/input_guard.py` 的规则 4 强制（违反即被护栏拦下）：

| 约束 | 落点 |
|---|---|
| **只读** | SQLite 用 `mode=ro` 打开，`save()` 在连接层就失败；HTTP 写方法一律 405 |
| **只绑定回环地址** | 传 `0.0.0.0` 直接拒绝启动（退出码 2） |
| **运行时零新增依赖** | 只用标准库 + 原生 HTML/CSS/JS，无框架、无构建步骤 |

页面区分**四种**状态：`数据源正常` / `部分数据源失败` / `数据源全部失败` /
`未记录数据源状态`。最后一类专指 v1.2 之前生成的老日报。

时间一律按**浏览器所在时区**（即本机时区）渲染：采集层的绝对时刻带明确偏移
（GitHub 是 `+00:00`、飞书考勤是 `+08:00`），页面统一换算后显示；`generated_at`
是本地墙上时间，原样显示。换算只发生在页面（`index.html` 的 `stampParts()` 一带），
采集层与视图层都保留完整 ISO 偏移不动 —— 视图层若擅自本地化，页面再换算一次就会
错上两倍偏移。

代码分层：`webview/views.py`（纯逻辑，可脱离 HTTP 单测）、
`webview/app.py`（HTTP 薄壳，只做请求→视图→响应的翻译）、
`webview/static/`（`index.html` + `app.css`，原生单页，两级：#/ 列表、#/report/<日期> 详情）。

### 设计阶段留档：`design/`

v1.2 的前端是用 **OpenDesign** 迭代出来的，过程留了档：

| 路径 | 是什么 |
|---|---|
| `design/brief/` | 交给 OpenDesign 的输入（6 份材料，原样存档） |
| `design/mock/opendesign-output.html` | OpenDesign 的产出原型（851 行、自包含、零外部依赖） |
| `design/screenshots/` | 落地后的真实页面截图 |
| `design/README.md` | 整条链条 + **「设计稿 → 代码」差异对照表** |

关键结论（细节见 `design/README.md`）：设计稿的 36 个 design token 被逐字照搬进
`webview/static/app.css` 的 `:root`；不落地的是设计稿里标注「评审用」的辅助块；
6 处 `13px` 归到 `--fs-12`。取舍理由追加记录在 `specs/adrs/004-展示层技术选型.md`。

### 测试文件与书 7.5.6 的对应

书的 7.5.6「Step 5 全量回归」点名了 9 个预期测试文件，本项目逐一对应：

| 书中列出的文件 | 用例数 | 覆盖内容 |
|---|---|---|
| `tests/test_github.py` | 12 | GitHub 采集层 |
| `tests/test_lark_task.py` | 7 | 飞书任务采集 |
| `tests/test_lark_msg.py` | 23 | 飞书群消息采集（关键词过滤 + 敏感词剔除 + v1.8.2 五种时间戳形态 + v1.8.3 排除应用自身消息） |
| `tests/test_attendance.py` | 20 | 飞书考勤采集（v1.1 新增） |
| `tests/test_generator.py` | 52 | 日报生成（含 v1.1 考勤逻辑、v1.3 双 ID 身份映射与「今日无记录」渲染、v1.5 提交摘要截断与条目分组、v1.6 邮件卡片版式与计数/总结句、v1.7 配色白名单与成员名条、v1.8 工程进展段与可配主题、v1.8.4 考勤行缺失侧呈现、v1.9 概览下的代码变更行数、v1.10 分板块空态与最近一次提交、v1.11 正文标题与主题同源、v1.15 邮件撤掉提交明细与协作沟通板块） |
| `tests/test_repo_progress.py` | 32 | 工程进展采集（v1.8 新增，自造临时 Git 仓库）：仓库定位、当日计数、版本号取最高、变更起点判定、闭环判定、规范覆盖率注入与降级、v1.10 最近一次提交日期 |
| `tests/test_email.py` | 7 | SMTP 推送 |
| `tests/test_lark_bot.py` | 8 | 飞书机器人推送 |
| `tests/test_main.py` | 22 | `main.py` 入口：编排、`load_dotenv`、`load_tasks_file`、CLI 退出码、路径锚定、v1.3 考勤名单接线、v1.4 仅工作日开关默认关闭、v1.11 正文标题与邮件主题同源 |
| `tests/test_pipeline.py` | 25 | 交付流水线采集（v1.13 新增：只读工作流/Dockerfile/tag；v1.14 增公开 Check Runs 的结论聚合、远端标签与注入取数） |
| `tests/test_integration.py` | 12 | 端到端集成：全链路、单源失败不阻塞、v1.4 周末/节假日默认照常生成 |

另有 11 个测试文件覆盖第 8~10 章的 Agent 设计模式与治理层（书里未规定文件清单）：
`test_agents.py`、`test_team_practice.py`、`test_contracts.py`、`test_framework.py`、
`test_suitability.py`、`test_tool_configs.py`、`test_harness.py`、`test_shared.py`、
`test_dockerfile.py`（13 个用例）、`test_workflows.py`（15 个用例），以及 v1.2 新增的 `test_webview.py`（50 个用例）。

在本目录下，书中那条命令可以一字不改地运行：

```bash
python -m pytest tests/ -v
```

## v1.3 迭代：把飞书考勤真正接上（起点在 design，不在代码）

第三次真实需求变更。触发点是**邮件里写着「数据获取失败：飞书考勤」，而飞书侧
其实只是"当天没打卡"** —— 这正是书里图 7-8 那条"失败 → 判定是代码 bug 还是
规范缺陷"分支。

1. **定位起点**：先查代码，发现调用方 `main.py` 从来没把成员名单传给
   `lark_attendance.collect()`，飞书回业务错误 `1220001 employeeNos is empty`，
   被当成"采集失败"。但再往下查发现**规范本身也不够用**：任务/消息接口返回的是
   `open_id`，考勤接口只接受 `employee_id` / `employee_no`（传 `open_id` 报
   `99992402`），而 `design.md` 里只有一个 `members[].lark` 字段 —— 一个字段
   服务不了两种匹配。→ 属**架构/契约变更**，起点上提到 `design.md`。
2. **更新 `design.md` §3.1**：成员身份映射拆成两个字段 ——
   `lark`（飞书 open_id，服务 `TaskRecord.assignee` / `MessageRecord.sender`）与
   `lark_employee_id`（飞书员工 ID，**只**服务考勤），并写清为什么必须拆。
3. **更新契约**：`contracts/data-models.md` §6 映射规则拆成两条；
   `contracts/api-spec.yaml` 的 `Member` schema 同步加字段，版本 `1.3.0`。
4. **更新 `tasks.md`**：Task 6 / Task 9 / Task 11 各补 v1.3 验收标准 —— 其中两条
   针对本次暴露的**"采到了空数组不等于失败"**：考勤采集成功但本人无记录时，
   日报必须渲染「今日无记录」，「考勤数据暂不可用」只留给真正的采集失败。
5. **执行**：`main.py` 新增 `ATTENDANCE_EMPLOYEE_TYPE = "employee_id"` 与
   `_attendance_user_ids()`（从 `members[].lark_employee_id` 去重取名单）；
   `formatter.aggregate()` 增加 `by_lark_employee` 映射（空 ID 不入表）；
   `template.render_markdown()` 的考勤分支由两段变三段。
6. **全量回归 + 同一 commit**：`347 passed`（v1.3 当时的数字；v1.15 后为 481），规范与代码一起提交（书图 7-8 的闭环要求）。

真实跑通的证据（2026-10-09 那次）：日志从
`飞书考勤采集失败 error: ... employeeNos is empty` 变成
`飞书考勤采集完成 count 0` / `success: true`，日报里两个成员的考勤段都渲染成
「- 今日无记录」——**"没打卡"与"取不到数据"从此在文书上不再混为一谈**。

## v1.4 迭代：日报不再只限工作日（起点在 proposal，不在代码）

需求原话是"打卡生成周报不要仅限工作日，让双休日、节假日也可以正常运行"。按书里的
**变更起点规则**（`extract/chunk_11.md:237-246`）判定：这改变的是"产品做什么"，
属**需求变更** → 起点是 `proposal.md`，一路往下（design → contracts → tasks → 代码 → 测试）。

| 层 | 改了什么 |
|---|---|
| `proposal.md` §2.1 | "每个工作日 18:00 自动触发" → "由调度器按需触发；**产品本身不限制生成日期**" |
| `proposal.md` §3.3 | 原"识别非工作日并自动跳过"**拆成两条**：① 双休日/节假日照常生成；② 只有显式 `--working-days-only` 才跳过（验收项 28 → 29） |
| `design.md` §4 / §6.6 | `main.run(..., working_days_only: bool = False)`；新增"触发时机与运行日期"一节，写明默认不判断工作日、跳过时返回 0（**跳过不是失败**） |
| `contracts/api-spec.yaml` | 版本 `1.4.0`；`holidays` 的语义收窄为"仅 `--working-days-only` 下生效" |
| `tasks.md` | Task 9 补 4 条 v1.4 验收标准（含"`run()` 默认值必须是 `False`"） |
| 代码 | `main.py` 的门槛由 `if not is_working_day(...)` 改为 `if working_days_only and not is_working_day(...)`；新增 `--working-days-only`；`calendar.py` / `errors.py` 的 docstring 同步收窄措辞 |
| 测试 | 新增周六默认跑通、节假日默认不跳过两条集成测试；`test_main.py` 新增"开关默认关闭"与"跳过时打印原因"两条 |

**设计取舍**：旧能力没删，改成了开关。理由是"今天该不该发日报"属于**调度策略**，
不是产品能力——写死进产品会让补跑、演练、值班日报都做不了。想恢复旧行为就加
`--working-days-only`。

真实跑通的证据：`python main.py --date 2026-10-11 --no-push`（**周日**）退出码 0，
`日报生成完成：2026-10-11，成员 2 人`；加上 `--working-days-only` 则打印
`[calendar] 2026-10-11 是非工作日，跳过日报生成（--working-days-only 已开启；…）` 并返回 0。

## v1.5 迭代：日报里的提交信息只留「标题 + 两三句摘要」（起点在 proposal）

需求原话是"代码提交后面加上我每次让你迭代出来的新功能的描述，描述务必要简单，
争取两三句话就完事"。判定依据：日报**呈现什么内容**属于产品行为 → **需求变更** →
起点 `proposal.md`，一路往下（design → contracts → tasks → 代码 → 测试）。

背后暴露的真实问题：本项目自己的提交正文（按书图 7-8 闭环写的规范链说明）有几十行，
`template.py` 原本把 `CommitRecord.message` **整段原文**贴进「代码提交」段，
一条提交就能把邮件撑成一屏半，收件人根本读不下去。

| 层 | 改了什么 |
|---|---|
| `proposal.md` §3.1 | 新增两条验收标准：① 只呈现"标题 + 正文摘要"，不得原样贴入整段正文；② 正文为空时只显示标题（验收项 29 → 31） |
| `design.md` §3.3 | 新增"提交信息在日报里的呈现"，把规则钉死：标题 = 第一行原文；摘要 = 正文首个非空段落的前 3 句、上限 200 字符；正文为空则摘要为空串；**截断只发生在渲染期**，落库仍是完整原文 |
| `design.md` §4 | 生成层接口新增 `template.summarize_commit(message, *, max_sentences=3, max_chars=200) -> tuple[str, str]` |
| `contracts/` | `data-models.md` 与 `api-spec.yaml` 的 `message` 说明补上"完整原文 + 渲染期截断"，api 版本 `1.5.0` |
| `tasks.md` | Task 6 补 5 条 v1.5 验收标准（含"完整正文不得出现在结果里""子列表项必须渲染成 `<li>`"） |
| 代码 | `template.py` 新增 `_first_paragraph()` / `_take_sentences()` / `summarize_commit()`；「代码提交」段改为"标题 + 缩进子列表项摘要"；`markdown_to_html()` 学会识别缩进列表项 |
| 测试 | `test_generator.py` 新增 6 条（19 → 25）：截前 3 句、无正文、`main.py` / `v1.2` 不被点号切碎、200 字符兜底、完整正文不进日报、缩进项变 `<li>` |

**这不是"AI 总结"**：截句是纯文本规则——只做"取第几段、取前几句、超长截断"，
不改写也不概括内容。同一份提交每次都渲染出同样的结果，所以能被测试钉住。

真实跑通的证据：同一份 2026-10-11 日报，改前「代码提交」段贴了 v1.4 那条提交的 40 多行正文；
改后是

```text
- `ppptouyu-wq/SDD-test` feat(v1.4): 日报不再仅限工作日，双休日与节假日照常生成 （+221/-38，17 个文件）
  - 需求变更（按书里的变更起点规则，起点在 proposal，不在代码）： 「打卡生成周报不要仅限工作日，让双休日、节假日也可以正常运行」。
```

### v1.5.1：把提交条目在 HTML 里拍平的呈现缺陷修掉

v1.5 上线后收件人反馈"几条提交挨在一起，容易看串"。查下来不是间距问题，是**渲染缺陷**：
`markdown_to_html()` 把所有 `- ` 项（无论缩进与否）都拍平成同一个 `<ul>` 下的同级 `<li>`，
于是 design.md §3.3 第 7 条要求的"标题下方**缩进**的子列表项"在 HTML 里根本看不出缩进——
摘要和提交标题长得一样平。按书图 7-8 判定：这是**规范与实现没对齐**（规范要求缩进，实现抹平了），
所以回溯收紧了 `design.md` §3.3 第 7/8 条与 `tasks.md` Task 6 的验收标准，再改代码。

| 层 | 改了什么 |
|---|---|
| `design.md` §3.3 | 第 7 条明确"必须是**嵌在父 `<li>` 内部的嵌套 `<li>`**，不能拍平成同级项"；新增第 8 条"相邻提交之间必须一眼可分：顶层条目有间距、摘要相对标题缩进且弱化字色" |
| `tasks.md` Task 6 | 原"渲染成 `<li>`"细化为"渲染成嵌套的 `<li>`"；新增一条"相邻提交在 HTML 里有明确视觉分隔，收件人不会看串"（tasks 验收项 92 → 93） |
| 代码 | `generator/template.py` 的 `markdown_to_html()` 改为**保序写出开始标签**：顶层项先写 `<li>…`，遇到缩进项就把 `<ul class="sub">` 嵌进父项内部，父项在子列表之后才 `</li>`；CSS 增加 `ul > li { margin-top: 10px }`（条目间距）与 `ul.sub > li { margin-top: 4px; color: #57606a }`（摘要缩进 + 弱化字色） |
| 测试 | 新增 `test_html_keeps_each_commit_visually_separated`：两条提交各带摘要时必须各自成顶层项、摘要嵌在自己父项内、顶层只有 1 个 `<ul>`、2 个 `<ul class="sub">` |

想恢复"摘要不缩进"的样子就去掉 `ul.sub` 那两条 CSS —— 但嵌套结构不能退，它是对契约的兑现。

## v1.6 迭代：邮件日报换成卡片式可视化版式（起点在 proposal）

需求原话是"就改用户能直接看到的日报，也就是邮件收到的那个，改的好看一点，像我给你图片的那样，
有颜色啥的，看着美观"，并附了一张参考邮件截图。判定依据：改的是"日报长什么样"，属于产品行为 →
**需求变更** → 起点 `proposal.md`，一路往下（design → contracts → tasks → 代码 → 测试）。

邮件正文原先由 `markdown_to_html(report.markdown)` 生成——那是"文档转换"，先天做不出配色与卡片。
本次改为**按结构直接渲染**：新增 `template.render_email_html(report)`，邮件是独立画布，
**不引用展示层（`webview/`）的 `ant` 令牌**，红色 `--accent` `#d32029` 明确不用于邮件
（大面积红色在邮件语境里是告警，日报是日常汇报）。

| 层 | 改了什么 |
|---|---|
| `proposal.md` §3.1 | 新增三条验收标准：① 以"卡片 + 配色"的版式呈现；② 概览计数与总结句必须由真实数据现算，**不得由 AI 生成**、不得出现未采集的指标；③ 兼容主流邮件客户端（表格布局 + 内联样式，不用外部图片/CSS/Web 字体）（验收项 31 → 34） |
| `design.md` §4 | 生成层接口新增 `template.render_email_html(report: DailyReport) -> str` |
| `design.md` §6.7（新增） | 邮件版式的色板（`#1a6cf5` 品牌 / `#e8f1fd` 信息块 / `#f6f8fb` 中性块 / `#cf1322`+`#fdecec` 告警）、五段结构、`<table role="presentation">` + 内联样式且 CSS 里不得出现 `url(`、空态与失败态口径 |
| `design.md` §3.3 | 追加说明：v1.6 起**邮件正文不再走 Markdown 转换**；`markdown_to_html()` 与它的第 7/8 条要求（嵌套而非拍平）保留，仍是"把已存档 Markdown 转 HTML"的通用转换器（补发历史日报用得上） |
| `tasks.md` Task 7 | 补 5 条 v1.6 验收标准（版式、计数现算、邮件客户端友好、空态与失败告警、单测覆盖"有数据"与"全空"）（tasks 验收项 93 → 98） |
| 代码 | `generator/template.py` 新增 `overview_counts()` / `summary_sentence()` / `repo_sentence()` / `render_email_html()` 与一组邮件专用渲染函数（`_email_callout` / `_email_stat_card` / `_email_commit_item` / `_email_document`）；`generator/formatter.py` 改调 `render_email_html()` 生成 `report.html` |
| 测试 | `test_generator.py` 新增 6 条（26 → 32）：版式齐全、计数与总结句取真实数据、全 0 措辞、按仓库汇总句、邮件客户端友好性（无 `url(`/`<script`/`<img`）、失败源告警 + 空板块「今日无记录」 |

版式五段：标题区（团队名 + 蓝色短线 + 日期）→ 信息块（日期 / 生成时间）→
采集失败告警块 → `📊 今日概览` 三张计数卡 → `📌 今日总结` → 按成员分组的
`📝 代码提交` / `✅ 任务进展` / `🕘 考勤` / `💬 协作沟通`，空板块一律用中性底渲染「今日无记录」。
（v1.10 起空板块改为「分板块说清」的空态文案；v1.15 起邮件不再渲染「💬 协作沟通」，此处描述的是当时的样子。）

**"今日总结"不是 AI 写的**：`summary_sentence()` 是确定性模板句，只把三项真实计数填进去
（`共完成 3 次代码提交，整体进展平稳。`），同一份数据每次渲染结果相同，所以能被测试钉住——
这也是 `proposal.md` §2.2 把"智能摘要/AI 总结"列为排除项之后的必然选择。

## v1.7 迭代：邮件版式换色与结构微调（起点在 design.md）

v1.6 的邮件版式是照需求参考稿（一张手机邮件截图）做出来的，配色（蓝 `#1a6cf5`）与版式骨架
一眼就是那张截图的翻版。v1.7 按「配色不要一模一样、风格可以改一下」的要求换成本项目自己的
身份色，并补上两处结构特征。

| 层级 | 改了什么 |
|---|---|
| `design.md` §6.7 | 色板换成青碧（teal）系（`#0f766e` 主色 / `#0b5b55` 深色 / `#e7f4f1` 浅青底）；新增硬约束「邮件 HTML 里的颜色必须落在这张表内」；版式由五段合成四段（标题区吸收了原「信息块」） |
| `generator/template.py` | 色板常量换成 teal 并新增 `EMAIL_PALETTE` 白名单；新增顶部通栏与 `_email_member_bar()`；`_email_stat_card()` 改为浅青底 + 顶部深青条；`report_title()` 与 `render_markdown()` 首行不再拼「工作日报」后缀 |
| `tasks.md` | 只追加迭代记录（Task 7 的验收标准「邮件主题包含日期和团队名称」在新格式下依然成立，不改验收标准） |
| 测试 | `test_generator.py` 新增 2 条（32 → 34）：颜色必须落进 `EMAIL_PALETTE`、身份色与成员名条；原「平台研发组工作日报」的断言改成标题区断言 |

两个决定与理由：

- **报告名不再由模板拼后缀**。部署配置里的 `team_name` 给的就是完整名称（本机是
  「孔子博智能日报」），模板再拼一次会得到「孔子博智能日报工作日报」。现在邮件主题是
  `孔子博智能日报 2026-10-11`，正文大标题是 `孔子博智能日报`；日期仍然保留 ——
  一天一封，收件箱里要能分辨是哪天。
- **配色收进白名单**。`EMAIL_PALETTE` 与单测 `test_email_html_only_uses_the_design_palette`
  把「邮件里出现的颜色必须落在 §6.7 色板内」变成可执行约束，避免以后随手加一个色板外的
  颜色，版式慢慢跑偏成"谁都能改一点"的样子。

## v1.8 迭代：邮件加「今日工程进展」板块 + 可配邮件主题（起点在 design，配合 proposal）

触发点是一条真实反馈：「日报格式和内容做的太差，迭代速度也太慢，对 vibe coding 的理解也不好」。
排查出两件事：① 「任务进展」那一格**永远 0** —— 飞书任务接口的口径被实测证伪（`GET /task/v2/tasks`
只认 `user_access_token`、只返回"我负责的"任务，`project_id` / `updated_since` 从来没生效，
见 `docs/飞书接入诊断报告.md` 的修正结论）；② 项目**自己**一天之内从 v1.2 迭代到 v1.8，
邮件里却一个字都没体现。于是把那一格换掉：不再假装能从飞书任务取到数，改为从**本地 Git 历史**
取"同一条命令就能复现的事实"。

| 层级 | 改了什么 |
|---|---|
| `proposal.md` §3.1 | 新增 5 条验收标准：板块存在；数字必须来自可复现事实、**不得现场执行测试或估算**；变更起点按最上游规范层判定；取不到仓库信息时渲染「今日未记录工程进展」且不影响其他板块与 `sources`；邮件主题可按配置模板渲染 |
| `design.md` §3.4 | 新增 `RepoProgress` 数据模型（7 字段）与**变更起点判定表**（改了 `specs/proposal.md` → 需求变更；只有 `design.md`/`contracts/**` → 设计变更；只有 `tasks.md` → 任务变更；只改代码与测试 → 缺陷修复；多层都改取**最上游**），并写明它**不是数据源**、不参与 `sources` 四态 |
| `design.md` §4 | 新增 `repo_progress.collect(repo_path, since, until, *, coverage=None) -> CollectResult[RepoProgress]`；`generator.generate()` 补 `sources` 与 `progress` 两个入参 |
| `design.md` §6.7(2) / §6.8 | 邮件版式四段 → **五段**（标题区 / 今日概览 / 今日总结 / 今日工程进展 / 按成员分组）；主题改由 `email.subject_pattern` 模板渲染（占位符 `{team_name}`/`{date}`/`{grade}`/`{owner}`）；§6.8 写死取数规则、诚实性约束与空态 |
| `contracts/` | `data-models.md` 补 `DailyReport.progress` 与 `RepoProgress` 字段表；`api-spec.yaml` 版本 1.5.0 → 1.8.0、新增 `RepoProgress` schema |
| `tasks.md` | 新增 **Task 13**（12 条验收标准）、Task 7 补 3 条主题模板验收标准、任务总数 12 → 13 |
| `collector/repo_progress.py`（新） | 标准库 `subprocess` 调 git：`git rev-parse --show-toplevel` 定位仓库根、`git log --name-only` 取当日文件清单（`-c core.quotepath=false`，否则中文路径被转义）；**不引入新依赖** |
| `generator/template.py` | 新增 `_progress_callout()` 与第 4 段；`report_title()` 支持 `subject_pattern`/`grade`/`owner`，未知占位符原样保留 |
| `main.py` | 新增 `SOURCE_REPO_PROGRESS`；覆盖率用延迟 import 的 callable 注入（采集层不依赖治理层）；`sources` 与「全部数据源不可用」判定都**排除**这个板块 |
| 测试 | 新增 `tests/test_repo_progress.py`（29 条，自造临时 Git 仓库，不依赖本仓库历史）与 `test_generator.py` 的 6 条版式/主题测试 |

三个设计决定与理由：

- **`RepoProgress` 刻意不进 `sources` 四态**。它不是"外部协作平台"：取不到多半意味着"本机不是
  Git 仓库"，属于可解释的缺失。硬挤进 `sources` 会把整份日报打成 `partial`，还要连带改
  `data-models` 的键枚举、术语定义与展示页。现在 `progress=None` → 中性块「今日未记录工程进展」
  + 一条 WARNING，其他板块与状态完全不受影响。
- **里程碑数字只允许是"同一条命令能复现的事实"**。当日提交数、版本号、变更起点、规范链改动、
  闭环判定全部来自 git；规范覆盖率来自 `sdd_agents.metrics` 的静态统计。**绝不现场跑 pytest** ——
  日报要秒级生成，更不能把"测试全部通过"这类无法验证的结论写进给老师看的邮件。
- **这个板块只在邮件渠道出现**。展示页按 v1.2 的约定保持"总览 + 成员四段"不变；`RepoProgress`
  随 payload 一起落库，将来展示页要用，不必重新采集。

一键复现：`python main.py --date 2026-10-12 --no-push`（第 4 段会写当天真实的提交数与迭代版本）。

### v1.8.1：修掉变更起点判定在「项目嵌在子目录」布局下失效

真跑当天日报时（`python main.py`）发现邮件里写着「变更起点：缺陷修复」，但当天明明改过
`specs/proposal.md`。根因：git log 给出的路径是 `sdd-daily-report/specs/proposal.md`，
而判定表比的是常量 `specs/proposal.md` —— 前缀对不上，规范文件一个都没被认出来，
需求变更被降级成「缺陷修复」，规范链与闭环判定也跟着失准。

按书图 7-8 判定：这不算纯代码 bug（判定表本身没写清"路径以什么为基准"），
所以同一个提交里既改代码，也回补规范。

| 层级 | 改了什么 |
|---|---|
| 规范 | `specs/design.md` §3.4：判定表补一句「路径先按项目根归一（截到 `specs/` 这一段）再比对」 |
| 代码 | `collector/repo_progress.py`：新增 `relative_to_project()`，判定与展示的规范链统一归一到项目根 |
| 测试 | `tests/test_repo_progress.py` 26 → 29（两组嵌套路径参数化 + 一条"临时仓库里项目嵌子目录"的端到端） |

### v1.8.2：修掉飞书消息时间静默退化成「采集时刻」

体检飞书群消息时（`_probe_lark_msg.py`）发现每条消息的时间都等于**探针执行的那一刻**。
打原始响应确认：飞书 `/im/v1/messages` 的 `create_time` 是**毫秒的数字字符串**
（`'1791893569132'`），而旧 `_parse_timestamp()` 只对 `int`/`float` 走纪元分支，
数字字符串会掉进 `datetime.fromisoformat()` 抛 `ValueError`，再静默 `return datetime.now()`
—— 于是日报里每条飞书消息的时间都成了采集时间，而不是发送时间。
旧测试只断言了 `isinstance(record.timestamp, datetime)`，所以这个 bug 一直没被发现。

规范没写错（`MessageRecord.timestamp = 发送时间`），**起点在代码**：补一个
`_epoch_to_datetime()`（`≥1e11` 判为毫秒，否则按秒），数字字符串也走这条路；
`tests/test_lark_msg.py` 补五种时间戳形态的参数化与一条"发送时间不是采集时间"的断言
（13 → 20 个用例）。

### v1.8.3：「协作沟通」只收真人发言

体检飞书群消息时发现：机器人推到群里的日报正文含「项目」「需求」，能过关键词过滤，
但它不是"协作沟通"。此前它只是被归属匹配**碰巧**丢掉（sender 不在成员身份映射表里），
规则上并没有说"不该收"——一旦成员表的 `lark` 里出现应用 ID，机器人自己的推送就会变成
某个成员的协作沟通。

- 起点（书图 7-8 的变更起点规则）：**需求变更 → proposal**。验收标准新增一条
  「「协作沟通」只呈现真人发送的消息」，随后在 `design.md` §4 把 `lark_msg.collect`
  的过滤口径写成四条（系统消息 → **应用自身消息** → 关键词 → 敏感词），最后落到
  Task 5 的三条验收标准上。
- 代码：`collector/lark_msg.py` 新增 `is_own_app_message(item, app_id)`，在关键词过滤
  **之前**排除 `sender_type == 'app'` 或 `sender.id` 为 `cli_` 前缀/本应用 `app_id` 的消息，
  并把排除条数写进「飞书消息采集完成」日志的 `own_app_excluded` 字段。
- 测试：`tests/test_lark_msg.py` 20 → 23（机器人消息即使命中关键词也不进结果、
  `cli_` 前缀兜底、真人消息照常按关键词过滤，并断言日志里的排除条数）。

### v1.8.4：考勤一直在「今日无记录」，其实是解析读错了键名

- **触发**：用户问「飞书有没有收到打卡信息」。探针打原始响应发现
  `POST /attendance/v1/user_tasks/query` 返回 **200 / code 0**，`data.user_task_results`
  里明明有记录 —— 而采集器返回 `success=True, count=0`。
- **根因**：v1.1 的解析代码按键名是**猜的**：读 `data.user_tasks` 与
  `check_in_time` / `check_out_time` / `check_date` / `status`，这些键在真实响应里
  都不存在。读不到就「成功地返回 0 条」，日报一路渲染成「今日无记录」——
  **看起来像"没打卡"，其实是"没解析到"**。
- **规范链改动**（缺陷修复 + 规范缺陷，同一个提交）：

| 层级 | 改了什么 |
|---|---|
| `design.md` §3.0.1（新增） | 真实响应结构、字段映射、纪元秒/毫秒的量级判别、多段班次合并规则、六行状态判定表、「解析不到必须出声」、缺失侧呈现口径 |
| `contracts/data-models.md` | `status` 枚举补 **签到缺失**（原枚举只有签退缺失这一侧） |
| `contracts/api-spec.yaml` | 同枚举同步；`behaviors` 补真实响应结构与两侧缺失口径 |
| `tasks.md` Task 11 | 新增 6 条 v1.8.4 验收标准（解析真实键、纪元秒、多段班次、签到缺失、无 `user_task_results` 必须 WARNING、夹具必须用真实结构） |
| `collector/lark_attendance.py` | 读 `user_task_results`；`_merge_shifts()` 取最早签到/最晚签退；`_parse_datetime()` 认数字字符串；`_parse_date()` 认整数 `YYYYMMDD`；响应结构变了打 WARNING 并带实际键名 |
| `generator/template.py` | 新增 `_attendance_span()`：缺哪一侧写「无签到」/「无签退」，不再把两侧都写成「签退缺失」 |
| `tests/test_attendance.py` | 12 → 17：夹具整体换成**实测响应结构**，补签到缺失、多段班次、缺勤、WARNING、纪元秒解析五组用例 |

- **为什么藏了这么久**：测试夹具是照着自己猜的结构写的，
  代码和测试**用同一套错误假设**，于是永远自洽。
- 一键复现：`python main.py --date 2026-10-13 --no-push`（考勤行应显示
  `状态：签到缺失（无签到 - 20:24）`）。

### v1.9：邮件「今日概览」下补一行「当日代码变更行数」

| 层级 | 改了什么 |
|---|---|
| `proposal.md` §3.1 | 新增 2 条验收标准：概览下方必须有当日代码变更行数汇总；当日无提交时显示 `+0 / -0 行`，不隐藏、不留白 |
| `design.md` §6.7(2) | 写清这一行的位置（三张计数卡下方）、求和口径（跨成员、跨仓库，取 `additions`/`deletions`）与"只出现在邮件渠道"的边界 |
| `tasks.md` Task 7 | 新增 2 条 v1.9 验收标准；迭代记录追加一句 |
| `generator/template.py` | 新增 `commit_line_totals(report)` 返回 `(新增, 删除)`、`_email_change_line(added, removed)` 渲染这一行；`render_email_html()` 在三张计数卡之后插入 |
| `tests/test_generator.py` | 42 → 44：跨成员跨仓库合计 + 顺序断言（计数卡 → 本行 → 今日总结）；无提交时 `+0 / -0` 不隐藏 |

**触发**：老师要求在邮件日报里"加一行能看见当天代码迭代提交的行数"，并看效果。
判定为**需求变更**（"呈现什么内容"属产品行为，不是缺陷修复）—— 起点在 `proposal.md`。

**为什么只放邮件、不放展示页**：邮件是每天发给收件人的成品，收件人只看得到这一屏；
展示页按 v1.2 定下的"总览 + 成员四段"保持稳定（与「今日工程进展」同理，见 `design.md` §6.8(1)）。

**一键复现**：`python main.py --date 2026-10-13 --no-push`，或本地起展示页对照。

### v1.10：空态分板块说清，无提交日补「最近一次提交」

| 层级 | 改了什么 |
|---|---|
| `proposal.md` §3.1 | 新增 3 条验收标准：各板块空态必须说清"空的是什么"；「今日工程进展」在当日 0 次提交时必须给出最近一次提交日期；两类文案都只能来自确定性模板句或本地 Git 事实；§3.3 的边界条目同步改为"给出该板块自己的空态说明" |
| `design.md` §3.4 | `RepoProgress` 增 `last_commit_date`，写清取数命令、口径（当日有提交 / 取不到一律空串，不许臆测）与动机 |
| `design.md` §6.7(5) | 新增「空态文案表」：代码提交／任务进展／协作沟通／考勤／工程进展各一句，并注明展示页口径不变 |
| `design.md` §6.8(1)(2) | 内容表补「最近一次提交」一行；取数实现补第二次 `git log -1` |
| `contracts/` | `data-models.md` 的 `RepoProgress` 表与 `api-spec.yaml` 的 schema 同步加 `last_commit_date` |
| `tasks.md` | Task 6 新增 4 条、Task 7 新增 4 条、Task 13 新增 3 条 v1.10 验收标准；迭代记录追加一句 |
| `generator/template.py` | 新增四个空态常量 `NO_COMMITS`/`NO_TASKS`/`NO_MESSAGES`/`NO_ATTENDANCE_RECORD`，Markdown 与邮件两条路径共用；`_progress_callout()` 在当日 0 次提交且日期非空时补「最近一次提交：YYYY-MM-DD」 |
| `collector/repo_progress.py` | 新增 `_read_last_commit(root, until)`（`git log -1 --until=<当日 00:00> --date=short`），只在当日无提交时调用 |

**触发**：2026-10-15 那封空日报上，四格齐刷刷写着「今日无记录」，收件人看不出空的是提交、任务还是数据；
「今日工程进展」也只有一句「当日 0 次提交」，分不清是今天没动还是项目停了三周。

**判定**：这是**需求变更**（"日报说什么"属产品行为）—— 起点在 `proposal.md`，规范与代码同一个提交。

**一键复现**：`python main.py --no-push`（空日报那天应看到四句各自的空态文案，以及「最近一次提交：…」）。

### v1.11：正文标题与邮件主题同源，主题去掉中间的 `+`

| 层级 | 改了什么 |
|---|---|
| `proposal.md` §3.1 | 主题模板的示例改为直连形式；新增一条验收标准：同一封邮件里正文标题区的标题必须与邮件主题一致 |
| `design.md` §4 | `generator.generate(...)` 与 `template.render_email_html(...)` 都增可选参数 `title`（空串回退旧形态） |
| `design.md` §6.7(2) | 标题区写清"与主题同源"：传入 `title` 时直接呈现该标题、不再另挂日期片段；主题段的分隔符口径更新 |
| `tasks.md` Task 7 | 新增 4 条 v1.11 验收标准；迭代记录追加一句 |
| `generator/template.py` | `render_email_html(report, *, title="")`：标题区与 HTML `<title>` 都用这一串 |
| `generator/formatter.py` | `generate(..., title="")` 透传给渲染器 |
| `main.py` | 用 `template.report_title()` + 同一份 `email` 配置算出标题，同时喂给正文与主题 |
| `config.yaml` | `subject_pattern` 改为 `"{grade}{owner}{date}智能日报"`（不再夹 `+`） |

**触发**：老师要求「主题把中间的 `+` 去掉，正文首页那个标题也改成一样的」。

**为什么加参数而不是各自拼**：主题由 `notifier/email.py` 从配置算，正文标题由渲染器画；
两边各写一套迟早会漂移（改了一处忘另一处，收件箱与正文就是两个名字）。
现在两处都走 `report_title()`，同一份配置、同一个函数。

**一键复现**：`python main.py --no-push`，或直接看 `data/reports.db` 里当天的 `html`。

### v1.12：CI/CD 流水线与镜像交付（课程 CI/CD 主题）

| 层级 | 改了什么 |
|---|---|
| `proposal.md` §2.1 | 新增「持续集成与镜像交付」：每次 push/PR 自动跑三项目测试与规范治理；打 `v*` 标签构建镜像并推送到 GHCR |
| `proposal.md` §2.2 | 新增排除项：**不做自动化部署**（交付到"镜像已发布"为止） |
| `proposal.md` §3.1/§3.2/§3.3 | 新增 8 条验收标准：测试红则不构建不推送、tag 含 7 位 sha 与版本号并输出 digest、非 root 且不含凭据、流水线不触网不发信、10 分钟内跑完、容器内无 `.git` 时工程进展降级、登录失败与推送被拒可分 |
| `proposal.md` §4 | 术语表补「持续集成 / 持续交付 / 镜像与 digest」三行 |
| `design.md` §7 | 新增整节：阶段划分与触发条件、镜像结构与 `.dockerignore` 口径、容器内工程进展降级、可追溯性与回滚、凭据边界、与展示层的关系 |
| `tasks.md` | 新增 Task 14（任务总数 13 → 14），验收标准 +12 条 |
| `.github/workflows/workspace-ci.yml` | 加固：依赖硬失败（去掉"失败也继续"的兜底）、`-o addopts=""`、junit 报告成 artifact、`concurrency` 取消旧运行、`permissions: contents: read`、补 `sdd_agents guard`、加镜像布局静态校验 |
| `.github/workflows/release-image.yml` | 新增：`test` → `build`（`needs: test`）→ `publish`（`needs: build`），推送 GHCR，打 `sha-<7 位>` / `vX.Y.Z` / `latest` 三种 tag，输出 digest |
| `Dockerfile` | 多阶段（builder / runtime）+ 非 root（uid 10001）+ `HEALTHCHECK`（镜像自检，不依赖挂载配置）+ `/app/data` 属主交给 `app` |
| `.dockerignore` | 新增：排除 `.git`、`data/`、`.env`、`config*.yaml`、测试与缓存 |
| `scripts/verify_dockerfile.py` | 扩到五项静态校验：多阶段、非 root、`HEALTHCHECK`、不跑 `--serve`、`.dockerignore` 挡住凭据 |
| `tests/test_workflows.py` | 新增 15 条：触发条件、`needs` 链、不含失败兜底、junit、权限、GHCR 与 sha 标签、digest、镜像名强制小写 |
| `tests/test_dockerfile.py` | 7 → 13 条：补多阶段 / 非 root / `HEALTHCHECK` / 不跑 `--serve` / `.dockerignore` / 运行期不装 pytest |

**触发**：课程主题转到 CI/CD，要求"提交后自动测试、通过后打包镜像、推到镜像仓库"，并明确"先不要部署和收尾"。

**为什么测试与构建必须分成两个 job**：放在一个 job 里跑完所有事，测试红了镜像照样已经推上去了。拆开并显式 `needs`，才能让"测试不过就不产出制品"成为机制，而不是靠人记得。

**为什么到 GHCR 就算完**：镜像推送到仓库后已经是"随时可部署"的制品 —— 这正是 Continuous Delivery 的终点。自动上生产（Continuous Deployment）要先有目标机器，本期明确排除，不写成"以后再说"。

**一键复现**：`python scripts/verify_dockerfile.py`（本机无 Docker 也能校验镜像布局）；`python -m pytest tests/test_workflows.py -q`（流水线结构断言）。

### v1.13：邮件新增「交付流水线」板块 + 非工作日不再误判「缺勤」

| 层级 | 改了什么 |
|---|---|
| `proposal.md` §3.1/§3.3 | 新增 5 条：交付流水线板块（阶段链 + 只能来自真实文件与 Git 事实 + 不得写"已跑通"）、`pipeline=None` 的中性空态、非工作日不判缺勤 |
| `design.md` §3.5 | 新增 `PipelineStatus`（`ci_workflows` / `release_to_registry` / `image_stages` / `image_nonroot` / `image_healthcheck` / `latest_tag` / `last_run_status`）与取数口径，写明 `last_run_status` **允许为空**（令牌无 `actions:read`，实测 403） |
| `design.md` §6.7(6) | 板块位置：工程进展之后、按成员分组之前；只在邮件渠道 |
| `design.md` §3.0/§3.0.1 | 考勤状态枚举加「休息日」；判定顺序改为"先判今天该不该上班，再判有没有打卡" |
| `contracts/` | `data-models.md` 加 `PipelineStatus` 与 `DailyReport.pipeline`；`api-spec.yaml` 加同名 schema、`status` 枚举补「休息日」，版本 1.13.0 |
| `tasks.md` | Task 7 加 6 条、Task 11 加 4 条 v1.13 验收标准；迭代记录追加一句 |
| `shared/models.py` | 新增 `PipelineStatus` 与 `DailyReport.pipeline`（默认 None，旧数据不受影响） |
| `collector/pipeline.py` | 新增：只读 `.github/workflows/*.yml`、`Dockerfile` 的 `FROM`/`USER`/`HEALTHCHECK`、`git tag --sort=-v:refname`；不联网、不调 Actions |
| `generator/template.py` | `_pipeline_callout()` + 板块渲染；空态常量 `PIPELINE_EMPTY="未记录交付流水线"`、`PIPELINE_RUN_UNKNOWN="未采集（令牌无 actions:read 权限）"` |
| `collector/lark_attendance.py` | `resolve_status(..., working_day=)`、`collect(..., working_day=True)` 与 `STATUS_REST_DAY="休息日"` |
| `main.py` | 用 `is_working_day(day, config.report.holidays)` 算 `working_day` 传给采集层；`pipeline` 挂到日报上 |
| 测试 | 新增 `tests/test_pipeline.py`（10 条）；`test_attendance.py` 17 → 20；`test_integration.py` 的考勤桩补 `working_day` 形参 |

**触发**：老师收到 2026-10-17（周六）那封日报，考勤写着「工时 0.0h，状态：缺勤（无签到 - 无签退）」——
周末没打卡被判成旷工；同时日报里看不出这个仓库有 CI/CD，所以补了一个如实呈现的「交付流水线」板块。

**为什么"首跑结果"写「未采集」**：查 Actions 结论需要 `actions:read` 权限，日报进程手里的令牌只有仓库读权限
（实测 `/actions/jobs/{id}/logs` 返回 403）。取不到就写未采集，**绝不**把"文件都在"说成"流水线已跑通"。

**v1.14 更正**：上面这段的结论错了 —— 403 的是「作业日志」接口，`/commits/{sha}/check-runs` 是公开接口，匿名即可读。v1.14 起「最近一次运行」直接显示真实结论与 7 位提交哈希。

**一键复现**：`python main.py --no-push`；或 `python -m pytest tests/test_pipeline.py -q`。

### v1.14：交付流水线的「最近一次运行」改成真实结论

| 层级 | 改了什么 |
|---|---|
| `proposal.md` §2.1/§3.1/§3.3 | 新增 7 条：读公开 Check Runs 拿真实结论、结论必须带 7 位提交哈希、聚合口径（成功/失败/进行中）、读不到写「未采集（公开 Check Runs 接口读不到）」、最新版本标签远端优先并回退本地 `git tag`、接口失败只影响该行 |
| `design.md` §3.5 | 整段重写：三个公开接口、`PipelineStatus` 增 `last_run_commit`、聚合口径表、注入 `http_get` 便于测试；写明 v1.13 与 v1.14 的结论为何不同 |
| `design.md` §6.7(6) | 行名改「最新版本标签」「最近一次运行」，示例 `成功（8 项全部通过），提交 4ebbcbf` |
| `contracts/` | `data-models.md` 加 `last_run_commit`；`api-spec.yaml` 版本 1.14.0、schema 加该字段 |
| `tasks.md` | 新增 Task 15（回溯补登采集模块，任务数 14 → 15，9 条验收标准）；Task 7 加 4 条 v1.14 验收标准 |
| `shared/models.py` | `PipelineStatus` 增 `last_run_commit: str = ""` |
| `collector/pipeline.py` | 新增 `_http_get_json()`、`check_runs_summary()`、`latest_remote_tag()`；`collect(..., slug=, token=, api_base=, timeout=, http_get=)`，`slug` 为空时**一次请求都不发** |
| `generator/template.py` | `PIPELINE_RUN_UNKNOWN` 改为「未采集（公开 Check Runs 接口读不到）」；行名改「最新版本标签」「最近一次运行」，有哈希才拼「，提交 」 |
| `main.py` | 把 `config.github.repos[0]` 当 `slug`、令牌当可选 `token` 传给采集层 |
| 测试 | `tests/test_pipeline.py` 12 → 25 条（聚合口径四态、版本号比较、注入取数、无 slug 不联网、邮件呈现） |

**触发**：v1.13 那封日报写「首跑结果：未采集（令牌无 actions:read 权限）」，但实测
`GET /repos/{owner}/{repo}/commits/{sha}/check-runs` **匿名就能拿到 200** —— 403 的是
`.../actions/jobs/{id}/logs`（**日志**要权限，**结论**不要）。一句话写错，整封邮件都会跟着错。

**为什么结论必须带提交哈希**：只写「成功」无法复核 —— 不知道成功的是哪一次提交。
7 位哈希让收件人能原样回查 `https://github.com/<slug>/commit/<sha>`。

**为什么「最新版本标签」远端优先**：容器里 `.dockerignore` 排掉了 `.git`，本地 `git tag`
在镜像中是空的；远端 `/tags` 才是"已经以版本号发布过哪些镜像"的真凭据，取不到才退回本地。

**一键复现**：`python main.py --no-push`（日志出现 `run=成功（8 项全部通过）@4ebbcbf`）；
或 `python -m pytest tests/test_pipeline.py -q`。

### v1.15：邮件板块精简（起点在 proposal.md）

| 层级 | 改了什么 |
|---|---|
| `proposal.md` §2.1/§3.1/§3.3 | 新增 3 条：邮件「代码提交」只留 per-repo 汇总句、不再列主要变更；成员分组只留代码提交/任务进展/考勤、不再有「协作沟通」；撤板块不等于藏失败 —— 飞书消息采集失败仍要顶部告警 |
| `design.md` §6.7(5) | 成员分组四段改三段，补三段说明（为什么收件人不需要逐条明细、为什么空格比不出现更吵、不渲染≠不采集）；空态表「协作沟通」行注明只用于 Markdown 与展示页 |
| `tasks.md` | Task 7 加 3 条 v1.15 验收标准（迭代记录补 v1.15） |
| `generator/template.py` | 删 `_email_commit_item()`（唯一调用点已撤）；成员循环里「主要变更：」标题 + 提交列表换成一句 `repo_sentence()` 汇总；删掉 `SECTION_MESSAGES` 板块与它的 `NO_MESSAGES` 空态；`CommitRecord` 不再被引用，一并从 import 去掉 |
| `config.yaml`（不提交） | 删掉 `name: 我（本地）` 这个成员条目 —— 它把同一个人的同一天数据渲染成第二张成员卡，纯属重复 |
| 测试 | `test_generator.py` 49 → 52 条：邮件无「主要变更」/无提交标题但保留汇总句、邮件无协作沟通而同一份 Markdown 仍有、消息采集失败仍在顶部告警；原有两条断言 `NO_MESSAGES in html` 的用例改为 `not in`（这正是本次改动暴露的"规范改了、断言没跟上"） |

**触发**：收件人反馈邮件太长 —— 逐条提交标题（含摘要）把一封日报撑成两屏，而"今天做了多少"
其实一句话就够；「协作沟通」在本项目的真实数据里长期是空的（群以机器人通知为主），
一格空格比不出现更吵。

**为什么只撤邮件、不撤 Markdown**：飞书群里的 Markdown 日报是"可翻阅的存档"，
明细和讨论在那里有查证价值；邮件是"扫一眼的报告"，两者受众不同，渠道口径因此分开
（`proposal.md` §3.1 v1.15 明写"仍保留在飞书 Markdown 日报与展示页"）。

**为什么不能顺手把采集也关掉**：不渲染 ≠ 不采集。飞书消息采集失败照样出现在顶部
「数据获取失败」告警里 —— 撤掉板块不能成为掩盖故障的借口（`proposal.md` §3.3 v1.15）。

**一键复现**：`python -m pytest tests/test_generator.py -q`（52 passed）；
真跑 `python main.py --no-push` 后打开邮件 HTML 就只剩三段。

## 工程化配套（第 3、8、9 章）

- `CLAUDE.md` —— AI 的持久上下文锚点（规范索引 + 核心约束）
- `.claude/settings.json` —— Hooks：写文件后自动跑 `pytest`
- `scripts/input_guard.py` —— 入护栏：禁止改 `specs/`、禁止越界实现、禁止硬编码密钥
- `scripts/output_guard.py` —— 出护栏：自动跑全量测试，失败即拦截
- `.github/workflows/spec-check.yml` —— 最小规范 CI（三个层次的规范检查）
- `Dockerfile` —— 内网服务器容器化部署（Cron 触发，非常驻服务）

本项目不包含（`proposal.md` § 2.2 明确排除）：智能摘要/AI 总结、移动端、历史日报统计图表、个人日报手动编辑、多租户架构。
