# 《SDD 实战：规范驱动开发之道》复现

本仓库是对黄佳《SDD 实战：规范驱动开发之道》（人民邮电出版社，2026）的完整复现。
三个子项目各自独立、可单独运行；另有若干方法论产物。

**目录**：[不含任何凭据](#-先看这里本仓库不含任何凭据) ·
[结果输出在哪](#运行结果输出在哪) · [子项目](#子项目) ·
[怎么运行](#怎么运行从本仓库根目录) · [什么时候会发邮件](#什么时候会发邮件--推飞书) ·
[要自己配什么](#发邮件--推飞书需要自己配什么) · [章节覆盖](#全书章节覆盖) ·
[与书中的对应关系](#与书中要求的对应关系) · [提交历史](#关于提交历史)

---

## ⚠️ 先看这里：本仓库不含任何凭据

**你要跑起来发邮件 / 推飞书，必须自己配置。** 仓库里只有 `.example` 占位模板。

| 项目 | 仓库里有什么 | 你要做什么 |
|---|---|---|
| 飞书 `app_id` / `app_secret` | **没有**。`.env.example` 里是空的 `LARK_APP_ID=` | 填进自己的 `.env` |
| 飞书群 `chat_id` | 占位 `oc_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` | 在 `config.github.yaml` 里换成自己的 |
| QQ 邮箱**授权码** | **没有**。`.env.example` 里是空的 `SMTP_PASSWORD=` | 填进自己的 `.env` |
| 邮箱账号 / 收件人 | 占位 `your-account@qq.com` / `leader@example.com` | 在 `config.github.yaml` 里换成自己的 |

**为什么没有**（两层保护，都要靠）：

1. 真实配置文件 `config.yaml`、`config.github.yaml`、`config.email.yaml`、
   `tasks.sample.json`、`.env` **全部被 `.gitignore` 排除**，`git ls-files` 里 0 个；
2. 密钥一律从**环境变量**读（`shared/config.py` 的 `GITHUB_TOKEN` / `LARK_APP_ID` /
   `LARK_APP_SECRET` / `SMTP_PASSWORD`），**从不写进配置文件**。

所以即使有人翻遍本仓库全部历史，也拿不到任何真实凭据。

> **想让邮件或飞书通知发出去，就自己去配**——见下面「发邮件 / 推飞书需要自己配什么」。

---

## 运行结果输出在哪

**只有一个地方：SQLite 数据库**——`sdd-daily-report/data/reports.db`，表 `daily_reports`。

- **一天一条记录**（`report_date` 是主键），同一天重跑**覆盖**，不堆重复；
- 每条记录里同时存 **`markdown`**（Markdown 版日报）、**`html`**（邮件正文版）、
  **`payload`**（结构化 JSON）三份；
- 用 **`--push`** 发出去时，邮件进对方邮箱、消息进飞书群，**本地不留副本**。

**写入时机**（`main.py:113-177`）：采集 → 聚合 → 生成 → **写库（`store.save`，第 151 行）
→ 之后才推送**。所以推送失败时数据库里照样有这条记录。

三种情况**根本不写库**：① 非工作日直接返回；② 所有数据源都失败（不生成空日报）；
③ `--check` 健康检查模式。

终端里那几行 JSON 是**日志**（走 stderr），不是结果。日报**不生成 md / html 文件**。

**怎么看日报内容**——用 `_show_report.py` 导出成文件（Markdown + HTML，HTML 双击即用浏览器看）：

```bash
cd sdd-daily-report
python _show_report.py              # 导出全部记录
python _show_report.py 2026-08-20   # 只导指定日期
# → data/reports/<日期>.md 与 data/reports/<日期>.html
```

不想用脚本就直接查库：

```bash
python -c "import sqlite3;print(sqlite3.connect('data/reports.db').execute('SELECT report_date,team_name FROM daily_reports').fetchall())"
```

> **`data/` 整个目录被 `.gitignore` 忽略，不会上传**——日报里含真实仓库名和群消息。
> 导出的 md / html 同样落在 `data/` 内，也不会进仓库。

### 展示页（v1.2 迭代）：在浏览器里看日报

不想导出文件，也可以直接起一个本地只读网页：

```bash
cd sdd-daily-report
python main.py --serve              # 浏览器打开 http://127.0.0.1:8000/
python main.py --serve --port 8010  # 换端口
python main.py --serve --db 其他库.db  # 换日报库
```

这是**第 7 章「需求变更的 SDD 流程」的一次完整演练**：先按图 7-4 自上而下更新
`proposal.md` → `design.md` → `tasks.md`，再按图 7-8 的闭环落地代码与测试。产物：

- `specs/proposal.md` §2.1/§2.2/§3.1：新增需求、收紧排除项、新增验收标准；
- `specs/design.md` §3.2/§4/§6.5：视图模型、`status` 推导、接口签名与三条展示层约束；
- `specs/contracts/data-models.md`、`specs/contracts/api-spec.yaml`：同步视图模型与枚举；
- `specs/tasks.md` Task 12；`specs/adrs/004-展示层技术选型.md`（ADR 只追加）；
- `webview/`：`views.py`（纯逻辑）/ `app.py`（标准库 HTTP 薄壳）/ `static/`（原生单页）。
- `design/`：**设计阶段留档** —— 交给 OpenDesign 的输入（`brief/`）、它的产出原型
  （`mock/opendesign-output.html`）、落地后截图，以及一份「设计稿 → 代码」差异对照表。
  想核"这个前端到底是不是按设计稿做的"，看这一份就够。

三条硬约束（`design.md` §6.5，由 `scripts/input_guard.py` 的规则 4 强制）：

1. **只读**——`SQLite` 以 `mode=ro` 打开，写操作在连接层就失败；HTTP 写方法一律 405；
2. **只绑定回环地址**——传 `0.0.0.0` 会被拒绝启动（退出码 2）；
3. **运行时零新增依赖**——只用 Python 标准库 + 原生 HTML/CSS/JS，无框架、无构建步骤。

页面上会区分**四种**日报状态：`数据源正常` / `部分数据源失败` / `数据源全部失败` /
`未记录数据源状态`。最后一类专指 **v1.2 之前生成的老日报**——它们没有记录数据源成败，
"没记录"不等于"全部失败"，所以单独成一态，不混进告警色阶。

页面上的时间统一按**你这台机器所在时区**渲染：采集自 GitHub / 飞书的时间带明确偏移
（如 `2026-10-05T06:38:17+00:00`，UTC），页面换算成本地时间再显示；本地产出的
`generated_at` 本来就是本地时间，原样显示。列表行与详情头部的生成时间精确到分钟
（`2026-10-05 14:40`），因此同一张卡片上的时间不会互相矛盾。

---

## 子项目

| 目录 | 内容 | 测试 |
|---|---|---|
| `sdd-daily-report/` | **主项目**「智能日报生成器」（第 4-7 章）。含 Agent 设计模式、团队实践与度量、适用性自评、Harness 审计、本地只读展示页（v1.2 迭代） | 340 通过 |
| `kb-search/` | **案例项目**「知识库语义搜索工具」（第 1-2 章） | 109 通过、1 跳过 |
| `brownfield-demo/` | **Brownfield 四步法**演示（第 7.4 节） | 50 通过 |

合计 **499 个测试**。一键运行：

```bash
python run_all_tests.py     # 需先安装 pytest
```

> 三个子项目都有名为 `tests` 的同名包，无法由单次 `pytest` 一起收集，
> 因此用 `run_all_tests.py` 分别为每个项目启动一个 pytest 进程。

## 怎么运行

VS Code 打开本仓库根目录，按 <kbd>F5</kbd> 选启动配置即可（配置已随仓库提供）：

| 配置 | 作用 |
|---|---|
| ① 演练：mock 数据（无需凭据） | **先跑这个**，用内置数据跑通全链路，不推送 |
| ② 真实 GitHub 仓库（2026-04-24，不推送） | 拉真实提交，需 `GITHUB_TOKEN` |
| ③ 健康检查（--check） | 逐项检查配置（缺凭据时报红属正常） |
| ④ 跑全部测试（三个项目） | 依次跑 `sdd-daily-report` / `kb-search` / `brownfield-demo` |
| ⑤ 真实发邮件（mock 数据，会真的发信） | 用 `config.email.yaml` 演练推送 |
| ⑥ 展示页：本地只读查看已生成的日报（v1.2） | 起本地网页看日报，只读、只绑定回环地址 |

> 如果改成把 `sdd-daily-report` 单独当工作区打开，用的是它自带的
> `sdd-daily-report/.vscode/launch.json`（7 个配置：把 ④ 换成「今天（真实推送）」，
> 另有「跑全部测试（pytest）」「只跑当前打开的测试文件」，⑥ 顺延为 ⑦）。
> VS Code 只读工作区根目录下的 `.vscode/`，两份配置各管各的场景，不会互相覆盖。

命令行等价写法（**从任意目录都可以**）：

```bash
# 从仓库根
python sdd-daily-report/main.py --config sdd-daily-report/config.yaml --date 2026-08-20 --mock

# 从项目目录（等价）
cd sdd-daily-report
python main.py --config config.yaml --date 2026-08-20 --mock

# --serve 连配置都不需要，它会自己找到 data/reports.db
python sdd-daily-report/main.py --serve
```

两点要注意：

- **`main.py` 不依赖工作目录**。入口的默认配置（`DEFAULT_CONFIG`）、默认日报库
  （`DEFAULT_STORAGE`）和配置里的相对 `storage_path` 都锚在项目根上，所以从仓库根、
  项目目录或任意位置调用结果一致（回归测试：
  `sdd-daily-report/tests/test_main.py::test_project_paths_are_anchored_at_the_project_root_not_cwd`）。
  但 **`python -m pytest` 仍要在项目目录里跑** —— 三个子项目有同名顶层包（`shared`、
  `generator`），怎么跑请看根目录的 `run_all_tests.py`。
- **真实配置不在仓库里**。`config.yaml`、`config.github.yaml`、`tasks.sample.json`
  都被 `.gitignore` 排除（含路径等环境信息），仓库里只有 `.example` 模板。
  F5 会自动从 `.example` 复制一份；手动跑需先自行复制：

  ```bash
  cd sdd-daily-report
  cp config.yaml.example config.yaml
  ```

  凭据一律通过环境变量注入（`GITHUB_TOKEN`、`LARK_APP_ID`、`LARK_APP_SECRET`、
  `SMTP_PASSWORD`），**不要写进配置文件**。

### 什么时候会发邮件 / 推飞书

推送的判定只有一行代码（`main.py:443`）：

```python
push = (not args.mock) if args.push is None else args.push
```

| 你的命令 | 会不会发 |
|---|---|
| `python main.py --date 2026-08-20` | ✅ **会发**（正式运行默认推送） |
| `python main.py --date 2026-08-20 --mock` | ❌ **不发**（mock 演练默认不推送，防误发） |
| 加 `--push` | ✅ 发，不管前面有没有 `--mock` |
| 加 `--no-push` | ❌ 不发，不管是不是正式运行 |

`--push` 与 `--no-push` 互斥（`main.py:398-405`），同时给会报错。
`--check` 走 `main.py:451` 的独立分支，**只检查配置，不发任何东西**。

**能真正发出去，还需四个条件全部满足**：

1. 当天是**工作日**——周末/节假日 `main.py:121` 直接返回，连采集都没开始；
2. **至少一个数据源成功**——全失败则不生成日报，提前返回（`main.py:127-135`）；
3. 判定为要推送（上表）；
4. **凭据齐备**——见下一节。

**邮件和飞书两个渠道互相独立**（`main.py:166-175` 逐个 try/except）：一个失败不影响
另一个，也不会让整次执行失败。所以会出现 `推送：email=成功、lark_bot=失败` 这种组合，
失败原因在日志里写明。

### 发邮件 / 推飞书需要自己配什么

**四样东西，全部在本地加，都不会进仓库。**

**① 密钥放进 `.env`**（复制 `.env.example` 改名为 `.env`，`main.py` 启动时自动加载）：

```
GITHUB_TOKEN=          # 可选；公有仓库配额只有 60 次/小时
LARK_APP_ID=你的飞书应用 app_id
LARK_APP_SECRET=你的飞书应用 app_secret
SMTP_PASSWORD=你的 QQ 邮箱授权码     # 注意是"授权码"，不是登录密码
```

> `.env` 加载规则：**只填尚未设置的变量**，已存在的环境变量优先（方便命令行临时覆盖）。
> 不想用 `.env` 就直接设环境变量：`$env:SMTP_PASSWORD = "你的授权码"`（仅当前窗口有效）。

**② 邮箱参数放进 `config.email.yaml`**（复制 `config.yaml.example` 改名）：

- `smtp_host` / `smtp_user` / `sender` → 你的 SMTP 服务器
  （QQ 邮箱是 `smtp.qq.com` + 你自己 `xxx@qq.com`；模板里是 `smtp.example.com` 这类占位值，**连不上**）
- `recipients` → 你自己的收件邮箱（模板里是 `leader@example.com`）

**③ 飞书参数放进 `config.github.yaml`**：

- `lark.chat_id` → 你的群 ID（模板里是 `oc_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`）
- `lark.app_id` / `lark.app_secret` 留空即可，它们从环境变量读

**④ 缺了会怎样**——不崩，明确报错，原因可见不静默：

| 缺什么 | 日志里会看到 |
|---|---|
| 没配收件人 | `邮件推送跳过：未配置收件人` |
| `smtp_host` 还是占位值 | `[Errno 11001] getaddrinfo failed`（重试 3 次后返回 False） |
| 没设 `SMTP_PASSWORD` / 账号是占位值 | SMTP 登录失败（重试 3 次） |
| 没设飞书凭据 | `[lark:auth] 飞书凭据缺失：请设置环境变量 LARK_APP_ID / LARK_APP_SECRET` |

## 全书章节覆盖

| 章节 | 产物 |
|---|---|
| 第 1-2 章 | SDD 五大原则与六阶段工作流；`kb-search` 案例项目 |
| 第 3 章 | 工具链全景、四层生态、`docs/SDD开源框架对照.md`、`sdd_agents/framework.py`（图 3-7 决策树） |
| 第 4 章 | `specs/proposal.md` + `specs/contracts/api-spec.yaml` |
| 第 5 章 | `specs/design.md` + ADR-001/002/003 |
| 第 6 章 | `specs/tasks.md`（Task 1-12）+ 子智能体并行调度 |
| 第 7 章 | 三层测试、验证与迭代、v1.1 与 v1.2 两次需求变更迭代、Brownfield 补规范 |
| 第 8 章 | 生成—评审、层级委托、护栏三明治、条件路由；`sdd_agents/harness.py`（Harness 审计） |
| 第 9 章 | 三阶段/三角色/治理检查/度量；`team-templates/` |
| 第 10 章 | `sdd_agents/suitability.py`（SDD 适用性自评） |

## 与书中要求的对应关系

书里对"怎么验证"只给了一条命令（印刷 p.145，7.5.6 Step 5）：

```bash
python -m pytest tests/ -v
```

书图 7-8 把这个动作写进 SDD 迭代闭环的第 5 步，闭环条件是
**全部测试通过 + 规范与代码一致**；表 4-2 第 3 项「验收可测」要求
**每一条验收标准都能转化为自动化测试用例**。所以本复现把
`specs/proposal.md` 的验收标准 → `specs/tasks.md` 的任务验收清单 → 测试用例
作为主线，测试即验收依据。

在子项目自己的根目录里，书中命令可以一字不改地运行：

```bash
cd sdd-daily-report
python -m pytest tests/ -v
```

书 7.5.6 还点名了 9 个预期测试文件，本仓库逐一对应：

| 书中列出的测试文件 | 本仓库 | 用例数 |
|---|---|---|
| `tests/test_github.py` | ✅ | 12 |
| `tests/test_lark_task.py` | ✅ | 7 |
| `tests/test_lark_msg.py` | ✅ | 13 |
| `tests/test_attendance.py`（新增测试） | ✅ | 12 |
| `tests/test_generator.py`（含新考勤逻辑） | ✅ | 14 |
| `tests/test_email.py` | ✅ | 7 |
| `tests/test_lark_bot.py` | ✅ | 8 |
| `tests/test_main.py` | ✅ | 17 |
| `tests/test_integration.py` | ✅ | 10 |

除书列的 9 个之外，本复现为第 8~10 章的 Agent 设计模式与治理层另建了
10 个测试文件（`test_agents.py` 26、`test_team_practice.py` 32、`test_contracts.py` 30、
`test_framework.py` 21、`test_suitability.py` 20、`test_tool_configs.py` 20、
`test_harness.py` 16、`test_shared.py` 18、`test_dockerfile.py` 7），
以及 v1.2 迭代新增的 `test_webview.py` 50。

三点如实说明：

- **`run_all_tests.py` 是本仓库的包装脚本，不是书中要求。** 三个子项目都有名为
  `tests` 的顶层包，pytest 在仓库根统一收集会模块名冲突，所以只能逐项目起进程。
  在任一子项目根目录内，书中那条命令原样可用。
- **`main.py` 的 `--mock` / `--push` / `--check` / `--serve` 四种运行模式书中没有**，
  属于 `specs/design.md` 的实现便利（`--mock` 让你不配凭据也能看见全链路跑通，
  `--mock` 默认不推送是为了防止调试时误发信；`--serve` 是 v1.2 需求变更的产物）。
  它们不是验收依据，pytest 才是。

## 真实链路验证情况

**已真实验证**：GitHub 采集、邮件推送（QQ SMTP）、飞书群推送、飞书群消息采集、
飞书任务/通讯录/群列表接口、展示页（`--serve` 的 HTTP 端到端：列表 / 详情 / 静态资源 /
写请求 405 / 老日报判定为 `未记录数据源状态`）。

**未真实验证**：飞书考勤（请求格式已按官方文档校准，但测试租户无考勤员工数据）、
Qdrant / OpenAI Embedding（本地实现已验证）、性能验收 `<60s`（未在真实规模下测）。

> 复现过程中修正了 6 类真实缺陷，其中 4 类只在接真实接口后才暴露 ——
> 详见各项目 README 与 `sdd-daily-report/docs/飞书接入诊断报告.md`。

## 关于提交历史

本仓库为**快照式提交**：三个子项目的全部源码与文档已完整并入，
但各自的**逐条提交历史未并入**（复现环境里 `git subtree` 不可用）。
因此这里看不到每个模块"一次 Task 一个提交"的演进过程 ——
只有 1 个初始快照提交，加上之后 v1.2 需求变更的迭代提交。

各子项目的历史**不在本仓库内**（也未上传），它们各自记录在**独立的原始仓库**中：
`sdd-daily-report` 45 个提交、`kb-search` 2 个、`brownfield-demo` 2 个。

之所以没有把历史一并带上来：这些历史里出现过飞书 `app_id`、群 `chat_id` 等应用标识
（不是密钥，但既然公开上传，就没有必要带出去）。密钥本身自始至终只通过环境变量注入，
从未进入任何提交。

如需在自己的环境里保留完整历史，用 `git subtree` 把各项目并入：

```bash
git remote add daily <你的 sdd-daily-report 仓库地址>
git fetch daily
git subtree add --prefix=sdd-daily-report daily/main
```

## 不包含的内容

**原书的扫描件（PDF 与页面图）不在本仓库内** —— 书是版权作品。
本仓库只包含复现代码、规范文档与方法论产物。

飞书 `app_id` 等应用标识在文档中已替换为占位符；`app_secret`、QQ 邮箱授权码
自始至终只通过环境变量注入，**从未进入任何版本的代码或提交历史**。

书稿的逐页转录稿（`extract/`）**也不在本仓库内**——它同样是版权作品的全文，
公开与否请自行判断。

