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
   | ⑤ 跑全部测试（pytest） | 409 个用例 |
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
| `tests/test_lark_msg.py` | 20 | 飞书群消息采集（关键词过滤 + 敏感词剔除；v1.8.2 补消息时间的五种时间戳形态） |
| `tests/test_attendance.py` | 12 | 飞书考勤采集（v1.1 新增） |
| `tests/test_generator.py` | 41 | 日报生成（含 v1.1 考勤逻辑、v1.3 双 ID 身份映射与「今日无记录」渲染、v1.5 提交摘要截断与条目分组、v1.6 邮件卡片版式与计数/总结句、v1.7 配色白名单与成员名条、v1.8 工程进展段与可配主题） |
| `tests/test_repo_progress.py` | 29 | 工程进展采集（v1.8 新增，自造临时 Git 仓库）：仓库定位、当日计数、版本号取最高、变更起点判定、闭环判定、规范覆盖率注入与降级 |
| `tests/test_email.py` | 7 | SMTP 推送 |
| `tests/test_lark_bot.py` | 8 | 飞书机器人推送 |
| `tests/test_main.py` | 21 | `main.py` 入口：编排、`load_dotenv`、`load_tasks_file`、CLI 退出码、路径锚定、v1.3 考勤名单接线、v1.4 仅工作日开关默认关闭 |
| `tests/test_integration.py` | 12 | 端到端集成：全链路、单源失败不阻塞、v1.4 周末/节假日默认照常生成 |

另有 10 个测试文件覆盖第 8~10 章的 Agent 设计模式与治理层（书里未规定文件清单）：
`test_agents.py`、`test_team_practice.py`、`test_contracts.py`、`test_framework.py`、
`test_suitability.py`、`test_tool_configs.py`、`test_harness.py`、`test_shared.py`、
`test_dockerfile.py`，以及 v1.2 新增的 `test_webview.py`（50 个用例）。

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
6. **全量回归 + 同一 commit**：`347 passed`（v1.3 当时的数字；v1.8.2 后为 409），规范与代码一起提交（书图 7-8 的闭环要求）。

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

## 工程化配套（第 3、8、9 章）

- `CLAUDE.md` —— AI 的持久上下文锚点（规范索引 + 核心约束）
- `.claude/settings.json` —— Hooks：写文件后自动跑 `pytest`
- `scripts/input_guard.py` —— 入护栏：禁止改 `specs/`、禁止越界实现、禁止硬编码密钥
- `scripts/output_guard.py` —— 出护栏：自动跑全量测试，失败即拦截
- `.github/workflows/spec-check.yml` —— 最小规范 CI（三个层次的规范检查）
- `Dockerfile` —— 内网服务器容器化部署（Cron 触发，非常驻服务）

本项目不包含（`proposal.md` § 2.2 明确排除）：智能摘要/AI 总结、移动端、历史日报统计图表、个人日报手动编辑、多租户架构。
