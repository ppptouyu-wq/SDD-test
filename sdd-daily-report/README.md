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
| 什么时候不写 | ① 非工作日直接返回；② 所有数据源都失败（不生成空日报）；③ `--check` 模式 |
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
   | ⑤ 跑全部测试（pytest） | 340 个用例 |
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
| `…是非工作日，跳过日报生成` | 日期落在周末或配置的节假日，属预期行为（proposal.md §3.3） |
| 运行成功但 0 条记录 | 该日期的采集窗口内确实没有提交，换 `--date` |
| 飞书报"凭据缺失" | 正常：未配 `LARK_APP_ID/SECRET`，日报会标注"数据获取失败"，其余数据源照常 |
| 邮件报"发送失败" | 未设 `SMTP_PASSWORD`，或用的不是 SMTP 授权码（QQ 登录密码不可用） |
| 直接运行 `main.py` 不带参数 | 会跑"今天 + 真实推送"，且当天若是周末会直接跳过 |

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

### 测试文件与书 7.5.6 的对应

书的 7.5.6「Step 5 全量回归」点名了 9 个预期测试文件，本项目逐一对应：

| 书中列出的文件 | 用例数 | 覆盖内容 |
|---|---|---|
| `tests/test_github.py` | 12 | GitHub 采集层 |
| `tests/test_lark_task.py` | 7 | 飞书任务采集 |
| `tests/test_lark_msg.py` | 13 | 飞书群消息采集（关键词过滤 + 敏感词剔除） |
| `tests/test_attendance.py` | 12 | 飞书考勤采集（v1.1 新增） |
| `tests/test_generator.py` | 14 | 日报生成（含 v1.1 考勤逻辑与布局顺序） |
| `tests/test_email.py` | 7 | SMTP 推送 |
| `tests/test_lark_bot.py` | 8 | 飞书机器人推送 |
| `tests/test_main.py` | 17 | `main.py` 入口：编排、`load_dotenv`、`load_tasks_file`、CLI 退出码、路径锚定 |
| `tests/test_integration.py` | 10 | 端到端集成：全链路、单源失败不阻塞、非工作日跳过 |

另有 10 个测试文件覆盖第 8~10 章的 Agent 设计模式与治理层（书里未规定文件清单）：
`test_agents.py`、`test_team_practice.py`、`test_contracts.py`、`test_framework.py`、
`test_suitability.py`、`test_tool_configs.py`、`test_harness.py`、`test_shared.py`、
`test_dockerfile.py`，以及 v1.2 新增的 `test_webview.py`（50 个用例）。

在本目录下，书中那条命令可以一字不改地运行：

```bash
python -m pytest tests/ -v
```

## 工程化配套（第 3、8、9 章）

- `CLAUDE.md` —— AI 的持久上下文锚点（规范索引 + 核心约束）
- `.claude/settings.json` —— Hooks：写文件后自动跑 `pytest`
- `scripts/input_guard.py` —— 入护栏：禁止改 `specs/`、禁止越界实现、禁止硬编码密钥
- `scripts/output_guard.py` —— 出护栏：自动跑全量测试，失败即拦截
- `.github/workflows/spec-check.yml` —— 最小规范 CI（三个层次的规范检查）
- `Dockerfile` —— 内网服务器容器化部署（Cron 触发，非常驻服务）

本项目不包含（`proposal.md` § 2.2 明确排除）：智能摘要/AI 总结、移动端、历史日报统计图表、个人日报手动编辑、多租户架构。
