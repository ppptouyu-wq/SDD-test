# 智能日报生成器 —— 任务清单

## 元信息
- 关联规范: specs/proposal.md、specs/design.md
- 任务总数: 14
- 预计总执行时间: 5到6h（包含测试）
- 执行策略: 按依赖关系顺序执行，独立任务可并行执行
- 迭代记录: v1.1 新增 Task 11 并修改 Task 6/Task 10；v1.2 新增 Task 12；v1.3 补齐 Task 11 的成员名单接线（修改 Task 6/Task 9/Task 11）；v1.4 取消默认的非工作日跳过，改为显式 `--working-days-only`（修改 Task 9）；v1.5 收紧提交信息在日报里的呈现（修改 Task 6）；v1.5.1 修复提交条目在 HTML 里拍平、看串的呈现缺陷（修改 Task 6）；v1.6 邮件日报改为卡片式可视化版式（修改 Task 6/Task 7）；v1.7 邮件版式换色与结构微调（起点在 design.md §6.7，未改任务的验收标准）；v1.8 新增 Task 13（工程进展采集与邮件板块），并把邮件主题改为可配置模板（修改 Task 7）；v1.8.1 修复变更起点判定在「项目嵌在子目录」布局下失效；v1.8.2 修复飞书消息时间静默退化成采集时刻（起点在代码，未改任务）；v1.8.3 「协作沟通」只收真人发言、显式排除应用自身消息（修改 Task 5）；v1.8.4 修复考勤解析读错响应键名导致真实打卡被静默丢成「今日无记录」（修改 Task 11）；v1.9 邮件「今日概览」下增一行当日代码变更行数汇总（修改 Task 7）；v1.10 空态分板块说清、工程进展在无提交日给出最近一次提交日期（修改 Task 6/Task 7/Task 13）；v1.11 邮件正文标题区与邮件主题同源（主题去掉中间的 `+` 改为直连，修改 Task 7）；v1.12 新增 Task 14（CI/CD 流水线与镜像交付：测试门禁、多阶段镜像、GHCR 发布）；v1.13 邮件新增「交付流水线」板块（如实呈现 CI/镜像/发布现状），并修掉非工作日误判「缺勤」的语义错误（修改 Task 7/Task 11）

---

## Task 1: 创建项目结构和基础配置

描述: 初始化项目目录结构，创建 pyproject.toml，安装核心依赖，创建配置文件模板

输入: specs/design.md（模块职责定义）
输出: 项目根目录结构+pyproject.toml+config.yaml.example
依赖: 无

验收标准:
- [ ] 目录结构符合 design.md §2 的模块划分
      （collector/、generator/、notifier/、shared/、tests/）
- [ ] pyproject.toml 包含 httpx、pyyaml、jinja2 依赖
- [ ] 依赖安装成功（书中原文为 `uv sync`；本复现改用 `pip install -e ".[dev]"`，
      因为目标环境未安装 uv。**这是一处有意偏离**，已在此登记）
- [ ] config.yaml.example 包含 members 映射表模板

---

## Task 2: 实现共享基础层

描述: 实现配置读取、日志记录、错误处理和 SQLite 存储4个共享模块

输入: config.yaml.example（配置格式）、design.md §6（非功能约束）
输出: shared/config.py、shared/logger.py、shared/errors.py、shared/storage.py
依赖: Task 1

验收标准:
- [ ] config.py 能读取 config.yaml 并返回配置对象
- [ ] config.py 在配置缺少必填字段时抛出明确的错误信息
- [ ] logger.py 输出 JSON lines 格式日志
- [ ] errors.py 定义 CollectorError、GeneratorError、NotifierError 三个自定义异常
- [ ] storage.py 能创建 SQLite，写入和查询日报记录
- [ ] 所有模块包含对应的单元测试，测试通过

---

## Task 3: 实现 GitHub 采集模块

描述: 调用 GitHub API 获取指定仓库在指定时间范围内的 Commit 记录，返回 CommitRecord 列表

输入: design.md §3（CommitRecord 数据模型）、design.md §4.1（采集层接口契约）
输出: collector/github.py+tests/test_github.py
依赖: Task 2

验收标准:
- [ ] collect() 函数签名符合 design.md §4.1 的接口定义
- [ ] 返回的每个 CommitRecord 包含全部7个字段且类型正确
- [ ] 支持分页查询（GitHub API 默认每页30条）
- [ ] API 超时重试3次（间隔5s），若仍失败则返回空列表+错误日志
- [ ] GitHub API 限流（HTTP 403）时，等待 reset 时间后重试
- [ ] 使用 Mock 数据的单元测试全部通过

---

## Task 4: 实现飞书任务采集模块

描述: 调用飞书开放平台 API 获取指定项目中当日状态变更的任务列表，返回 TaskRecord 列表

输入: design.md §3（TaskRecord 数据模型）、design.md §4.1（采集层接口契约）
输出: collector/lark_task.py+tests/test_lark_task.py
依赖: Task 2

验收标准:
- [ ] collect() 函数签名符合 design.md §4.1 的接口定义
- [ ] 返回的每个 TaskRecord 包含全部5个字段且类型正确
- [ ] 飞书 Token 过期时自动刷新后重试1次
- [ ] API 超时重试3次，若仍失败则返回空列表+错误日志
- [ ] 使用 Mock 数据的单元测试全部通过

---

## Task 5: 实现飞书消息采集模块

描述: 调用飞书开放平台 API 获取指定群的消息记录，按关键词过滤，返回 MessageRecord 列表

输入: design.md §3（MessageRecord 数据模型）、design.md §4.1（采集层接口契约）
输出: collector/lark_msg.py+tests/test_lark_msg.py
依赖: Task 2

验收标准:
- [ ] collect() 函数签名符合 design.md §4.1 的接口定义
- [ ] 返回的每个 MessageRecord 包含全部4个字段且类型正确
- [ ] 关键词过滤正确，只返回包含指定关键词的消息
- [ ] 敏感关键词（薪资、绩效等）被正确过滤，不出现在结果中
- [ ] 飞书 Token 过期时自动刷新后重试1次
- [ ] 使用 Mock 数据的单元测试全部通过

验收标准（v1.8.3 变更，协作沟通只收真人发言；见 design.md §4 的过滤口径）:
- [ ] 应用自身发送的消息（`sender.sender_type == "app"`，或 `sender.id` 等于本应用 `app_id`）**必须被显式排除**，即使它命中了配置里的关键词
- [ ] 排除发生在关键词过滤**之前**，且排除数量写进日志（便于核对"今天机器人推了几条"）
- [ ] 真人消息（`sender_type == "user"`，open_id 以 `ou_` 开头）不受影响，照常按关键词过滤

---

## Task 6: 实现日报生成模块

描述: 接收各成员的采集数据，按"代码提交→任务进展→协作沟通"组织日报内容，输出 Markdown 和 HTML 格式文件

输入: design.md §3（MemberReport、DailyReport 数据模型）、design.md §4.2（生成层接口契约）
输出: generator/formatter.py、generator/template.py+tests/test_generator.py
依赖: Task 2

验收标准:
- [ ] generate() 函数签名符合 design.md §4.2 的接口定义
- [ ] 输出的 DailyReport.markdown 包含三个部分标题
- [ ] 输出的 DailyReport.html 可被浏览器正确渲染
- [ ] 若某成员当日无任何记录，日报中需要显示"今日无记录"
- [ ] 若某数据源采集失败，日报中需要标注"数据获取失败"
- [ ] Markdown 到 HTML 的转换格式正确

验收标准（v1.1 变更，配合 Task 11）:
- [ ] 日报内容必须包含每位团队成员的工时统计
- [ ] 当考勤数据不可用时，界面需要显示 "考勤数据暂不可用"
- [ ] 布局顺序调整：工时统计模块应位于 "任务进展" 之后、"协作沟通" 之前
- [ ] 使用 Mock 数据的单元测试全部通过

验收标准（v1.3 变更，配合 Task 11 的名单接线）:
- [ ] 当多个成员名共用同一个飞书 ID（同一人的两种 Git 作者名写法）时，
      该 ID 的采集记录（考勤/任务/消息）必须回填到**所有**匹配的成员，
      不得因身份映射表按 ID 去重而只回填其中一人，其余显示"今日无记录"
- [ ] 考勤采集**成功但本人当日无记录**时，日报必须渲染"今日无记录"，
      不得把考勤段整段留白（留白会被误读为"当日没有考勤模块/没有考勤数据"）；
      "采集失败"才渲染"考勤数据暂不可用"，两种状态不能混为一谈

验收标准（v1.5 变更，提交信息的呈现；见 design.md §3.3）:
- [ ] `summarize_commit()` 把完整提交信息拆成「标题 + 摘要」：标题取第一行原文；
      摘要取正文第一个非空段落的前 3 句，总长不超过 200 字符，超出截断并补 "…"
- [ ] `render_markdown()` 渲染的「代码提交」段只呈现标题与摘要，且摘要渲染为标题下方
      缩进的子列表项；完整正文（几十行的规范链说明）**不得**出现在结果里
- [ ] 没有正文时 `summarize_commit()` 返回空摘要，日报只显示标题
      （不出现空占位、不把上一条提交的内容串下来）
- [ ] `CommitRecord.message` 仍保存完整原文（截断只发生在渲染期，换呈现方式不必重新采集）
- [ ] `markdown_to_html()` 把缩进的子列表项渲染成**嵌在父 `<li>` 内的嵌套 `<li>`**：既不能退化成 `<p>`，也不能拍平成与提交标题同级的项
- [ ] 相邻提交之间在 HTML 里有明确视觉分隔（顶层条目有间距、摘要相对标题缩进且字色更弱），收件人不会把两条提交看串（v1.5.1 呈现缺陷修复）

验收标准（v1.10 变更，空态分板块说清；见 design.md §6.7(5)）:
- [ ] `NO_COMMITS` / `NO_TASKS` / `NO_MESSAGES` / `NO_ATTENDANCE_RECORD` 四个空态常量分别渲染成「当日无代码提交」「当日无任务状态变更」「当日无符合条件的讨论」「当日无打卡记录」，不再统一渲染一句「今日无记录」
- [ ] Markdown 与邮件两条渲染路径用**同一组**空态常量，不得一边新一边旧
- [ ] 四段空态文案互不相同（用同一句话打发就等于没说明白空的是什么）
- [ ] 展示层（`webview/views.py` 的 `TEXT_NO_RECORD_TODAY`）口径**不变**，本变更只约束日报本体

---

## Task 7: 实现邮件推送模块

描述: 将 HTML 格式日报通过 SMTP 发送给指定收件人

输入: design.md §4.3（推送层接口契约）
输出: notifier/email.py+tests/test_email.py
依赖: Task 6

验收标准:
- [ ] send() 函数签名符合 design.md §4.3 的接口定义
- [ ] 邮件主题包含日期和团队名称
- [ ] 邮件正文为 HTML 格式的日报内容
- [ ] 发送失败时重试2次，仍失败则返回 False+错误日志
- [ ] 使用 Mock SMTP 的单元测试通过
- [ ] 邮件正文由 `template.render_email_html()` 按 design.md §6.7 的版式渲染：标题区、当日概览三张计数卡、今日总结、按成员分组的各板块（v1.6 变更）
- [ ] 概览计数（代码提交/任务更新/协作讨论）与今日总结句由 `DailyReport` 真实数据现算，不引入 AI 或任何外部服务（v1.6 变更）
- [ ] 邮件 HTML 的卡片与配色用 `<table>` + 内联样式表达；CSS 中不出现 `url(`（不引用外部图片/字体），邮件不依赖 JavaScript（v1.6 变更）
- [ ] 成员某板块当日为空时渲染「今日无记录」，采集失败的来源在顶部以告警块列出（v1.6 变更）
- [ ] 版式相关的单元测试通过，覆盖"有数据"与"四个板块全空"两种情形（v1.6 变更）
- [ ] 邮件主题由 `report_title()` 按 `report.subject_pattern` 模板渲染，默认 `{team_name} {date}`；占位符 `{team_name}`/`{date}`/`{grade}`/`{owner}` 均可用，未知占位符原样保留不报错（v1.8 变更，design.md §6.7(2)）
- [ ] `email.subject_pattern` / `email.grade` / `email.owner` 里的年级、姓名等个人属性只从配置取值，不出现在模板代码里（v1.8 变更）
- [ ] 邮件版式为**五段**：标题区、今日概览、今日总结、今日工程进展、按成员分组（v1.8 变更，design.md §6.7(2)）
- [ ] 「今日概览」三张计数卡下方有**一行当日代码变更行数汇总**：`commit_line_totals()` 跨成员、跨仓库对 `additions`/`deletions` 求和，渲染成「当日代码变更 +N / -M 行」（v1.9 变更，design.md §6.7(2)）
- [ ] `commit_line_totals()` 在当日没有提交时返回 `(0, 0)`，页面显示「+0 / -0 行」，不隐藏、不留白（v1.9 变更）

验收标准（v1.10 变更，空态与"最近一次提交"；见 design.md §6.7(5)、§6.8(1)(4)）:
- [ ] 成员板块为空时渲染该板块自己的空态文案（与 Task 6 的 v1.10 四条同一组常量），不再统一渲染「今日无记录」
- [ ] 「今日工程进展」在当日 0 次提交且 `RepoProgress.last_commit_date` 非空时，渲染一行「最近一次提交：YYYY-MM-DD」；该字段为空串时不渲染这一行，也不臆造日期
- [ ] `RepoProgress.last_commit_date` 在当日有提交时**不**渲染「最近一次提交」这一行（"最近一次"就是今天，没有增量信息）
- [ ] 空态文案与最近一次提交日期都来自确定性模板句或本地 Git 事实，不调用任何模型、不做估算（proposal.md §3.1 v1.10）

验收标准（v1.11 变更，正文标题与主题同源；见 design.md §6.7(2)）:
- [ ] `report_title()` 渲染出的主题串与 `render_email_html()` 标题区显示的标题**是同一个字符串**：`render_email_html(report, title=报题串)` 后，报题串原样出现在正文标题区，不出现第二份不同的名字
- [ ] 传入 `title` 时标题区不再另外渲染日期片段（`render_email_html()` 的结果里不出现"标题串 + 空档 + 日期"这种重复）
- [ ] `title` 为空串时回退到 v1.7 的形态（`team_name` 与日期同行），旧调用方与既有测试不受影响
- [ ] 邮件 HTML 的 `<title>` 元素也与主题同源（收件箱与邮件列表页显示的是同一个名字）

验收标准（v1.13 变更，交付流水线板块；见 design.md §3.5、§6.7(6)、§3.0）:
- [ ] 「今日工程进展」之后、按成员分组之前出现「交付流水线」板块，内容是一条阶段链「测试门禁 ──▶ 镜像构建 ──▶ 发布到镜像仓库 ──▶ 部署（本期不做）」+ 现状行（proposal.md §3.1 v1.13）
- [ ] 板块里的每一项都来自 `PipelineStatus` 的字段，且这些字段只由仓库文件与 Git 事实算出：工作流文件名、`Dockerfile` 的 `FROM`/`USER`/`HEALTHCHECK`、`git tag`
- [ ] `PipelineStatus.last_run_status` 为空串时渲染「未采集（令牌无 actions:read 权限）」，**不得**回填"已跑通"之类未经核实的结论（proposal.md §3.1 v1.13 第 2 条）
- [ ] `pipeline=None`（仓库里没有工作流、没有 Dockerfile、也没有版本标签）时渲染中性空态「未记录交付流水线」，不写成采集失败，也不影响其他板块（proposal.md §3.3 v1.13）
- [ ] `pipeline` 不出现在 `DailyReport.sources` 里，日报的四态判定不受影响（design.md §3.5）
- [ ] 该板块只在邮件渠道出现；展示页保持 v1.2 的"总览 + 成员四段"不变

---

## Task 8: 实现飞书推送模块

描述: 将 Markdown 格式日报通过飞书机器人 webhook 发送到指定群

输入: design.md §4.3（推送层接口契约）
输出: notifier/lark_bot.py+tests/test_lark_bot.py
依赖: Task 6

验收标准:
- [ ] send() 函数签名符合 design.md §4.3 的接口定义
- [ ] 发送的消息为 Markdown 格式
- [ ] 发送失败时重试2次，仍失败则返回 False+错误日志
- [ ] 使用 Mock webhook 的单元测试通过

---

## Task 9: 实现主编排入口

描述: 创建 main.py 作为系统入口，按顺序编排"采集→聚合→生成→推送"的完整流程

输入: 所有模块的接口
输出: main.py
依赖: Task 3、Task 4、Task 5、Task 6、Task 7、Task 8

验收标准:
- [ ] 按"采集→聚合→生成→推送"顺序编排，任一数据源失败不阻断其他数据源
- [ ] 支持 --date 指定日报日期，默认当天
- [ ] 支持 --check 健康检查模式（验证 GitHub/飞书/邮件配置）
- [ ] 支持 --mock 演示模式（无凭据时用内置数据跑通全链路）
- [ ] 识别非工作日（周末及法定节假日）并跳过日报生成
      —— **v1.4 起改为显式开关**：默认不跳过，见下方 v1.4 变更段
- [ ] 全局异常被捕获，执行状态写入日志

验收标准（v1.4 变更，触发时机与运行日期）:
- [ ] 默认情况下**不判断工作日**：周末与配置的法定节假日都能正常完成"采集→生成→推送"，
      不得因为日期是周六/周日/节假日而抛 `NonWorkingDayError` 或提前返回
- [ ] 只有在显式传入 `--working-days-only` 时，才在周末或 `report.holidays`
      命中的日期跳过；跳过时记 INFO 日志、打印跳过原因、返回码 0（跳过不是失败）
- [ ] `report.holidays` 只在 `--working-days-only` 下生效；默认模式下配置了
      节假日也必须照常生成（防止"以为填了就会跳过"的误用）
- [ ] `run(..., working_days_only=...)` 的默认值必须是 `False`，
      保证任何既有调用方（含测试与展示页链路）升级后行为是"每天都能跑"

验收标准（v1.3 变更，配合 Task 11）:
- [ ] 编排考勤采集时，必须把 config.yaml 中每位成员的飞书考勤 ID
      （`members[].lark_employee_id`）作为 `user_ids` 传给 `lark_attendance.collect()`；
      **不得以空名单调用**（空名单会让接口返回业务错误 `1220001 employeeNos is empty`，
      使"当天没打卡"被误报成"数据获取失败"）
- [ ] 必须显式指定 `employee_type`。飞书考勤接口只接受 `employee_id` / `employee_no`
      两种取值（实测 `open_id` 会被拒），本项目统一使用 `employee_id`，
      与 `members[].lark_employee_id` 中存放的飞书员工 ID 保持一致

---

## Task 10: 集成测试

描述: 编写端到端测试，验证从采集到推送的完整链路

输入: proposal.md §3.1 功能验收标准、design.md 接口契约
输出: tests/test_integration.py
依赖: Task 9

验收标准:
- [ ] 端到端测试覆盖"采集→聚合→生成→推送"全链路（全部使用 Mock）
- [ ] 日报包含"代码提交""任务进展""协作沟通"三个核心板块
- [ ] 单一数据源失败时，其余数据源正常，且日报标注"数据获取失败"
- [ ] 成员当日无记录时显示"今日无记录"
- [ ] 所有数据源均失败时不生成空日报
- [ ] 测试全部通过

---

## Task 11: 实现考勤数据采集模块（新增 v1.1）

描述: 对接飞书考勤 API，获取团队成员的签到与签退数据。

输入:
  design.md §3（AttendanceRecord 数据模型）
  design.md §4.1（采集层接口契约）
输出:
  collector/lark_attendance.py
  tests/test_attendance.py
依赖: Task 2

验收标准:
- [ ] collect() 函数签名严格符合 design.md 的接口定义
- [ ] 返回 CollectResult 对象（包含 success 标志位）
- [ ] 逻辑正确计算工时（check_out - check_in）
- [ ] 妥善处理缺失签退数据的情况（状态标注为 "签退缺失"）
- [ ] 当 API 不可用时，success 置为 False 并附带详细错误信息
- [ ] 基于 Mock 数据的单元测试全部通过

验收标准（v1.3 变更）:
- [ ] `user_ids` 是调用方的责任：采集模块只负责把它原样放进请求体，
      不得自行猜测或省略；调用方传空名单时必须以 `success=False` 显式失败，
      错误信息里带上飞书返回的原因，不得静默返回空结果
- [ ] `AttendanceRecord.employee_id` 必须回填到 config 中 `members[].lark_employee_id`
      与之相等的成员，使"当天确实没打卡"渲染为"今日无记录"，
      而不是"数据获取失败"

验收标准（v1.8.4 变更，解析真实响应结构）:
- [ ] 必须解析真实响应 `data.user_task_results[].records[]`（键名与 `design.md`
      §3.0.1 一致），不得再读不存在的 `data.user_tasks` /
      `check_in_time` / `check_out_time` / `status`
- [ ] `check_in_record` / `check_out_record` 的 `check_time` 是**纪元秒的数字字符串**，
      必须按量级判断（消息接口的 `create_time` 是毫秒，两处不同）；
      `day` 是整数 `YYYYMMDD`，`user_id` 即 `employee_id`
- [ ] 一天多段班次时，`AttendanceRecord` 取当日**最早签到**与**最晚签退**
- [ ] 签到缺失但有签退时 `status` 为"签到缺失"（v1.8.4 新增枚举）；
      两者都缺为"缺勤"
- [ ] 响应里既没有 `user_task_results` 也没有旧键 `user_tasks` 时，
      必须打 WARNING 并带上实际键名，不允许静默返回 0 条
      （"0 条"看起来完全合法，正因如此这个缺陷藏了很久）
- [ ] `tests/test_attendance.py` 的夹具必须使用真实响应结构（旧夹具是猜想的，
      所以锁不住这个缺陷）

验收标准（v1.13 变更，非工作日不判缺勤；见 design.md §3.0、§3.0.1）:
- [ ] `collect(..., working_day=False)`（周末 / 配置的法定节假日）时，**两侧都没有打卡**的记录状态为「休息日」，不再是「缺勤」（proposal.md §3.1 v1.13）
- [ ] `working_day=True`（默认值）时行为不变：两侧都缺仍判「缺勤」——不得因为这次改动把工作日的缺勤也放过
- [ ] 休息日里只缺一侧（有签到无签退 / 有签退无签到）仍按「签到缺失 / 签退缺失」判，不吞掉异常打卡
- [ ] `main.py` 用 `shared.calendar.is_working_day(day, config.report.holidays)` 的结果喂给采集层，采集层不自己读配置

---

## Task 12: 实现本地只读展示层（新增 v1.2）

描述: 从 SQLite 日报库读取已生成的日报，在本机浏览器中提供"列表 → 详情"两级只读浏览，
并把三种异常状态（数据源失败 / 考勤不可用 / 无日报记录）显式呈现在页面上

输入:
  design.md §3.2（视图模型）、design.md §4（展示层接口契约）
  design.md §6.5（展示层约束：只读 / 只监听回环地址 / 零新增依赖 / 设计令牌）
  contracts/api-spec.yaml 的 paths（三个只读端点）
  contracts/data-models.md §3（视图模型字段级契约）
输出:
  webview/__init__.py
  webview/views.py（读库 → 视图模型 → 状态判定，纯逻辑，不依赖 HTTP 设施）
  webview/app.py（把 HTTP 请求翻译成 views 调用）
  webview/static/index.html、app.css
  main.py 的 --serve / --port 参数
  tests/test_webview.py
依赖: Task 2（存储层）、Task 6（日报生成；本任务新增 DailyReport.sources 字段的读取）

验收标准:
- [ ] 展示层对 data/reports.db 只执行查询，全程无写操作（design.md §6.5(1)）
- [ ] 服务默认只绑定 127.0.0.1，不接受来自其他主机的连接（§6.5(2)）
- [ ] 未新增任何运行时依赖：不引入 Web 框架，无构建步骤，标准库即可启动（§6.5(3)、ADR-004）
- [ ] `GET /api/reports` 返回全部日报的列表项，按日报日期倒序（proposal.md §3.1 v1.2）
- [ ] `GET /api/reports/{date}` 返回该日详情；无记录时返回明确错误码而非空白页
- [ ] status 判定：sources 全为 true → `ok`；部分 false → `partial`；全 false → `failed`；为空 → `unknown`（design.md §3.2）
- [ ] `sources` 为空的历史日报判定为 `unknown`，页面标注"未记录数据源状态"而非"数据源全部失败"（design.md §3.2、ADR-004 补充记录）
- [ ] 详情页对失败数据源标注"数据获取失败"，且与"今日无记录"在视觉上可区分
- [ ] 成员 attendance 为 None 时显示"考勤数据暂不可用"
- [ ] 数据库中无任何日报时，首页显示空态提示，不报错、不白屏
- [ ] 对页面地址发起写操作（POST/DELETE 等）返回明确错误且不产生任何数据变更
- [ ] 页面视觉使用 design.md §6.5(4) 的 ant 设计令牌值，错误态用 `--danger` 而非 `--accent`
- [ ] 基于临时 SQLite 库的单元测试全部通过

---

## Task 13: 实现工程进展采集与邮件板块（新增 v1.8）

描述: 从**日报所在代码仓库本身**的本地 Git 历史里取数，产出团队级的"今日工程进展"
（当日提交数、迭代版本号、变更起点层级、规范链变更文件、闭环判定、规范覆盖率），
在邮件日报的"今日总结"之后新增该板块；取不到仓库信息时渲染中性空态，不影响其他板块与四态判定

输入:
  design.md §3.4（RepoProgress 数据模型、变更起点判定表、"不是数据源"的口径）
  design.md §6.8（取数规则：只允许 `git` 与 `metrics` 能复现的数字；空态；只走邮件渠道）
  design.md §6.7(2)（五段版式第 4 段、可配置的邮件主题模板）
  contracts/data-models.md §2（RepoProgress 字段级契约）
  proposal.md §3.1 的 v1.8 四条、§4「工程数据源」
输出:
  collector/repo_progress.py（**只用标准库 `subprocess` 调本机 git**，零新增依赖）
  shared/models.py 的 RepoProgress 与 DailyReport.progress
  shared/config.py 的 report.repo_path（工程进展）与 email.subject_pattern / grade / owner（邮件主题模板）
  generator/formatter.py（把 progress 挂到日报上）
  generator/template.py（邮件第 4 段 + 主题模板渲染）
  main.py（采集接线 + 覆盖率取数注入）
  tests/test_repo_progress.py、tests/test_generator.py（板块与主题）
依赖: Task 2（存储层）、Task 6（日报生成）、Task 7（邮件推送）

验收标准:
- [ ] `git rev-parse --show-toplevel` 自动定位仓库根；`report.repo_path` 非空时可显式覆盖（design.md §6.8(2)）
- [ ] 当日提交数与涉及的文件清单来自本地 `git log --since --until --name-only`，不调用 GitHub API（§6.8(2)）
- [ ] 变更起点层级按 design.md §3.4 的判定表给出；多层同时改动时取**最上游**那一层（proposal.md §3.1 v1.8）
- [ ] `RepoProgress.version`（迭代版本号）取当日提交标题里最高的 `vX.Y[.Z]`；当日没有带版本号的提交时为空串（§3.4）
- [ ] `same_commit` 仅在存在"同一提交同时改动 `specs/` 与代码/测试"时才为 true（书图 7-8 闭环）
- [ ] 规范覆盖率通过**注入的 callable** 取（未注入 → 空串）；采集层不 `import sdd_agents`（§6.8(2)）
- [ ] 板块只渲染可复现的事实：不得出现"测试全部通过""代码质量优秀"这类断言（§6.8(3)、proposal.md §3.1 v1.8）
- [ ] 取不到仓库信息（非 Git 仓库 / 本机无 git）时 `success=False`、`progress=None`，邮件渲染"今日未记录工程进展"并写 WARNING 日志（§6.8(4)）
- [ ] `progress` 不出现在 `sources` 中，日报的四态判定不受其影响（design.md §3.4）
- [ ] 邮件主题按 `email.subject_pattern` 渲染，默认 `{team_name} {date}`，`{grade}`/`{owner}` 由配置提供（§6.7(2)）
- [ ] 展示页保持 v1.2 的"总览 + 成员四段"不变（§6.8(1)）
- [ ] 基于临时 Git 仓库的单元测试全部通过（`git init` + 构造提交，不依赖本仓库的真实历史）

验收标准（v1.10 变更，最近一次提交日期；见 design.md §3.4、§6.8(1)）:
- [ ] `RepoProgress.last_commit_date` 只在当日 `commit_count == 0` 时填值，取 `git log -1 --until=<当日 00:00> --pretty=format:%ad --date=short` 的日期（`YYYY-MM-DD`）
- [ ] 当日有提交、或最近一次提交取数失败时，`last_commit_date` 为空串；**不得**退回当前时间之类的臆测值（仓库完全没有提交历史时 `git log` 本身就失败，`collect()` 显式返回 `success=False`，同样不会给出臆造日期）
- [ ] 该字段随 `RepoProgress` 一起落库（`payload`），将来展示页要用无需重新采集

---

## Task 14: 实现 CI/CD 流水线与镜像交付（新增 v1.12）

描述: 把"提交 → 自动测试 → 构建镜像 → 发布到镜像仓库"接成一条可复现的流水线：
测试阶段分别跑三个项目与规范治理四件事并产出 junit 报告；构建阶段用多阶段 Dockerfile
产出非 root、不含凭据的镜像；发布阶段在打 `v*` 标签时把镜像推送到 GHCR 并输出 digest。
**不含自动部署**（proposal.md §2.2）。

输入:
  design.md §7.1（阶段划分与触发条件、为什么测试与构建必须分段）
  design.md §7.2（镜像结构与 .dockerignore 口径、非 root、HEALTHCHECK、不常驻）
  design.md §7.3（容器内无 `.git` 时工程进展的降级边界）
  design.md §7.4（标签与 digest、回滚口径）
  design.md §7.5（凭据边界：只用 GITHUB_TOKEN，最小权限）
  proposal.md §3.1 v1.12 五条、§3.2 一条、§3.3 两条、§2.2「不做自动化部署」
输出:
  .github/workflows/workspace-ci.yml（加固：显式装依赖、`-o addopts=""`、pip 缓存、
    junit artifact、concurrency、最小权限、补 `sdd_agents guard`）
  .github/workflows/release-image.yml（tag 触发 → buildx → GHCR → digest）
  sdd-daily-report/Dockerfile（多阶段 + 非 root + HEALTHCHECK）
  sdd-daily-report/.dockerignore
  scripts/verify_dockerfile.py（扩到能校验多阶段与非 root）
  tests/test_dockerfile.py（同步扩测试）
依赖: Task 1（项目结构）、Task 9（编排入口与 `--check` 健康检查）

验收标准:
- [ ] `workspace-ci.yml` 在 push 与 PR 上触发，三个项目用矩阵分别跑 pytest；测试命令带 `-o addopts=""`，能被解析出 "N passed" 汇总行（proposal.md §3.1 v1.12、§3.2）
- [ ] CI 中依赖**显式安装**（httpx/PyYAML/Jinja2/pytest），不再用 `|| true` 兜底吞掉安装失败（design.md §7.1）
- [ ] 测试结果以 `junit` XML 作为 artifact 上传；工作流顶层 `permissions: contents: read`，`concurrency` 取消同分支的重复运行（§7.1、§7.5）
- [ ] 规范治理在 CI 中至少包含 `generate_tool_configs.py --check`、`sdd_agents review`、`sdd_agents team`、`sdd_agents guard`、`sdd_agents metrics`（§7.1）
- [ ] `release-image.yml` 只在 `push` 到 `main` 或打 `v*` 标签时构建，且构建 job 显式 `needs` 测试 job —— 测试红时不产出镜像（§7.1、proposal.md §3.1 v1.12）
- [ ] 镜像推送到 `ghcr.io`，标签至少含 `sha-<7 位>` 与 `vX.Y.Z`，并输出 `digest`（§7.4）
- [ ] 镜像内以**非 root 用户**运行；`.dockerignore` 排除 `.git`、`.github`、`tests`、`data`、`.env`、`config*.yaml`（§7.2、proposal.md §3.1 v1.12）
- [ ] `HEALTHCHECK` 调用 `python main.py --check`；`ENTRYPOINT`/`CMD` 保持"一次执行即退出"，镜像内不跑 `--serve`（§7.2、§7.6）
- [ ] 流水线只跑不触网的演示模式，不向真实飞书群或邮箱发信；日志不出现明文密钥（§7.5）
- [ ] 镜像仓库登录失败与推送被拒在日志里可区分，不静默跳过发布（proposal.md §3.3 v1.12）
- [ ] 容器内无 `.git` 时 `progress=None`、邮件渲染「今日未记录工程进展」，不影响其余板块（§7.3、proposal.md §3.3 v1.12）
- [ ] `scripts/verify_dockerfile.py` 与 `tests/test_dockerfile.py` 覆盖多阶段、非 root、HEALTHCHECK、`.dockerignore` 四项，本地无 Docker 也能校验（§7.2）

---

## 执行顺序与依赖关系

```
Task 1 ──▶ Task 2 ──┬──▶ Task 3  ┐
                    ├──▶ Task 4  ├──▶ Task 9 ──▶ Task 10
                    ├──▶ Task 5  ┘        ▲
                    ├──▶ Task 6 ──┬──▶ Task 7 ┘
                    │             └──▶ Task 8
                    │             └──▶ Task 12（v1.2 新增，只读旁路）
                    │             └──▶ Task 13（v1.8 新增，工程数据源）──▶ Task 7（v1.8 变更）
                    └──▶ Task 11（v1.1 新增）──▶ Task 6（v1.1 变更）
```

- **关键路径**：Task 1 → Task 2 → Task 3/Task 4/Task 5 → Task 6 → Task 7/Task 8 → Task 9 → Task 10
- **最大并行度**：3（Task 3 + Task 4 + Task 5 同时执行）
- Task 7、Task 8 可并行执行
- v1.1 增量路径：Task 2 → Task 11 → Task 6（考勤数据接入日报）
- v1.2 增量路径：Task 2 → Task 6 → Task 12（展示层读库渲染）
  —— 展示层不接入采集链路，只读 `data/reports.db`，因此**不在关键路径上**；
  它也可以与 Task 7/Task 8 并行执行。
- v1.8 增量路径：Task 2 → Task 13 → Task 7（工程进展进入邮件版式）
  —— Task 13 是本机旁路采集，不依赖 GitHub/飞书可用性，因此**不在关键路径上**，
  但它是 v1.8 邮件第 4 段的唯一数据来源，故 Task 7 的版式变更必须排在它之后。
- v1.12 增量路径：Task 1 → Task 14（流水线与镜像交付）
  —— Task 14 只读仓库内容、只在 CI 上执行，**不改变运行时行为**，因此不阻塞其他任务；
  但它依赖"项目结构与 `--check` 健康检查"已经存在，故排在 Task 1/Task 9 之后。

## 任务粒度自评（对应 §6.2）

| 任务 | 预计代码量 | 能否独立测试 | 判定 |
|---|---|---|---|
| Task 2 | ~150 行 | 是（4 个模块各有单测） | 合适 |
| Task 3 | ~120 行 | 是（Mock HTTP） | 合适 |
| Task 6 | ~180 行 | 是（Mock 数据） | 合适 |
| Task 9 | ~100 行 | 是（集成测试） | 合适 |
| Task 12 | ~200 行（含静态页面） | 是（临时 SQLite 库 + 视图模型断言） | 合适（略偏上沿，故逻辑与 HTTP 壳分离） |
| Task 13 | ~150 行 | 是（临时 Git 仓库 + 注入覆盖率取数） | 合适 |
| Task 14 | ~120 行（两个 workflow + Dockerfile + `.dockerignore` + 校验脚本） | 是（静态校验覆盖多阶段/非 root/HEALTHCHECK；真实构建由 Actions 承担） | 合适 |

均落在"50~200 行 / 产出可测试交付物"的最佳区间内。
