# 智能日报生成器 —— 任务清单

## 元信息
- 关联规范: specs/proposal.md、specs/design.md
- 任务总数: 12
- 预计总执行时间: 4到5h（包含测试）
- 执行策略: 按依赖关系顺序执行，独立任务可并行执行
- 迭代记录: v1.1 新增 Task 11 并修改 Task 6/Task 10；v1.2 新增 Task 12；v1.3 补齐 Task 11 的成员名单接线（修改 Task 6/Task 9/Task 11）

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
- [ ] 全局异常被捕获，执行状态写入日志

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

## 执行顺序与依赖关系

```
Task 1 ──▶ Task 2 ──┬──▶ Task 3  ┐
                    ├──▶ Task 4  ├──▶ Task 9 ──▶ Task 10
                    ├──▶ Task 5  ┘        ▲
                    ├──▶ Task 6 ──┬──▶ Task 7 ┘
                    │             └──▶ Task 8
                    │             └──▶ Task 12（v1.2 新增，只读旁路）
                    └──▶ Task 11（v1.1 新增）──▶ Task 6（v1.1 变更）
```

- **关键路径**：Task 1 → Task 2 → Task 3/Task 4/Task 5 → Task 6 → Task 7/Task 8 → Task 9 → Task 10
- **最大并行度**：3（Task 3 + Task 4 + Task 5 同时执行）
- Task 7、Task 8 可并行执行
- v1.1 增量路径：Task 2 → Task 11 → Task 6（考勤数据接入日报）
- v1.2 增量路径：Task 2 → Task 6 → Task 12（展示层读库渲染）
  —— 展示层不接入采集链路，只读 `data/reports.db`，因此**不在关键路径上**；
  它也可以与 Task 7/Task 8 并行执行。

## 任务粒度自评（对应 §6.2）

| 任务 | 预计代码量 | 能否独立测试 | 判定 |
|---|---|---|---|
| Task 2 | ~150 行 | 是（4 个模块各有单测） | 合适 |
| Task 3 | ~120 行 | 是（Mock HTTP） | 合适 |
| Task 6 | ~180 行 | 是（Mock 数据） | 合适 |
| Task 9 | ~100 行 | 是（集成测试） | 合适 |
| Task 12 | ~200 行（含静态页面） | 是（临时 SQLite 库 + 视图模型断言） | 合适（略偏上沿，故逻辑与 HTTP 壳分离） |

均落在"50~200 行 / 产出可测试交付物"的最佳区间内。
