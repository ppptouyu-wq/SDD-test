# CLAUDE.md

> 本文件是 AI 的持久上下文锚点（第 3 章）：规范文件索引 + 核心约束。
> 参考书中图 8-3 的 CLAUDE.md 示例结构。

## 项目规范

所有开发工作必须遵循以下规范文档：

- 需求规范：`specs/proposal.md`
- 架构设计：`specs/design.md`
- 任务清单：`specs/tasks.md`
- 接口契约：`specs/contracts/data-models.md`
- 架构决策：`specs/adrs/`

**规范是第一手工件，代码是规范的衍生物。** 当代码与规范冲突时，改代码去符合规范，而不是改规范去迁就代码。

## 开发规则

1. **不写规范外的功能**：`specs/proposal.md` § 2.2 明确排除了智能摘要、移动端、历史报表分析、多端隔离 —— 一律不做。
2. **不改 `specs/` 下的文件**：规范变更必须由人类确认。若实现中发现规范缺陷，先提出修改建议。
3. **按 tasks.md 逐任务执行**：一个任务 = 一个可验证的交付物（50~200 行）。
4. **每个模块先看 design.md 的接口契约**：函数签名必须与 `specs/design.md` § 4 一致。
5. **同时生成实现与测试**：验收标准（`- [ ]` 清单）要逐条转成测试用例。
6. **禁止硬编码密钥**：一律通过环境变量注入（`GITHUB_TOKEN`、`LARK_APP_ID`、`LARK_APP_SECRET`、`SMTP_PASSWORD`）。
7. **保持模块边界**：`collector/` 不做格式化，`generator/` 不做采集与推送，`notifier/` 不做数据处理。
8. **不突破展示层边界**（v1.2）：`webview/` 必须零新增运行时依赖（只用标准库）、只监听回环地址、只读；不得引入 Web 框架、不得实现登录与权限（`specs/design.md` § 6.5）。

## 目录结构

```
sdd-daily-report/
├── specs/            # 规范（第一手工件，人类所有）
│   ├── proposal.md
│   ├── design.md
│   ├── tasks.md
│   ├── contracts/
│   └── adrs/         # ADR 只追加、不修改
├── collector/        # 采集层：github / lark_task / lark_msg / lark_attendance / repo_progress（v1.8，本地 Git）
├── generator/        # 生成层：formatter / template
├── notifier/         # 推送层：email / lark_bot
├── shared/           # 共享层：config / logger / errors / storage / retry / calendar / models
├── webview/          # 展示层（v1.2）：views（纯逻辑）/ app（HTTP 薄壳）/ static（原生 HTML+CSS）
├── tests/            # 单元测试 + 集成测试
└── main.py           # 编排入口
```

## 当前状态

- 版本：v1.11（v1.11 让邮件正文标题与主题同源：`main.py` 用 `template.report_title()` 与同一份 `email` 配置算出标题串，既给 `notifier/email.py` 当主题、又经 `formatter.generate(title=...)` 传给 `render_email_html()` 画在标题区，收件箱与正文不会再是两个名字；主题模板去掉了中间的 `+`，见 `design.md` §6.7(2)；v1.10 让日报的空态说人话：`generator/template.py` 新增 `NO_COMMITS`/`NO_TASKS`/`NO_MESSAGES`/`NO_ATTENDANCE_RECORD` 四个常量，Markdown 与邮件共用，不再统一写「今日无记录」；`collector/repo_progress.py` 新增 `_read_last_commit()`，当日 0 次提交时补「最近一次提交：YYYY-MM-DD」，让「今天没动」与「停了三周」能区分，见 `design.md` §3.4/§6.7(5)/§6.8；v1.9 给邮件「今日概览」补一行当日代码变更行数：`template.commit_line_totals(report)` 跨成员、跨仓库对 `additions`/`deletions` 求和，渲染成「当日代码变更 +N / -M 行」，当日没有提交时显示 `+0 / -0 行`（不隐藏、不留白）；这一行只在邮件渠道出现，展示页保持 v1.2 的「总览 + 成员四段」不变，见 `design.md` §6.7(2)；v1.8.4 修掉考勤解析读错响应键名的缺陷：旧代码读的是猜想的 `data.user_tasks` 与 `check_in_time`/`check_out_time`/`check_date`/`status`，真实键是 `data.user_task_results`、时刻在 `records[].check_in_record.check_time`（**纪元秒的数字字符串**）、日期是整数 `day` —— 读不到就「成功地返回 0 条」，真实打卡被静默丢成「今日无记录」；现按 `design.md` §3.0.1 的 真实结构解析，多段班次取最早签到 + 最晚签退，补「签到缺失」状态，响应结构变了打 WARNING 并带实际键名，呈现时缺哪一侧就写哪一侧；v1.8.3 让「协作沟通」只收真人发言：`collector/lark_msg.py` 新增 `is_own_app_message()`，在关键词过滤前排除 `sender_type == 'app'`（或 `sender.id` 为 `cli_` 前缀 / 本应用 `app_id`）的消息，并把排除条数写进日志 —— 以前机器人自己推的日报是**碰巧**被归属匹配丢掉的，规则上并没有「不该收」，见 `design.md` §4 的过滤口径与 `proposal.md` §3.1；v1.8.2 修掉飞书消息时间静默退化成「采集时刻」的缺陷：飞书 `/im/v1/messages` 的 `create_time` 是**毫秒的数字字符串**，旧 `_parse_timestamp()` 只认 `int`/`float`，数字字符串抛 `ValueError` 后静默兜底成 `datetime.now()`；现补 `_epoch_to_datetime()` 并让数字字符串走同一条路（`collector/lark_msg.py`，规范未改 → 起点在代码）；v1.8.1 修掉变更起点判定在「项目嵌在子目录」布局下整体失效的缺陷：路径先按项目根归一（截到 `specs/` 这一段）再比对，否则 `sdd-daily-report/specs/proposal.md` 比不过常量 `specs/proposal.md`，需求变更会被误判成「缺陷修复」、规范链一片空白（真跑当天日报时发现，`design.md` §3.4 补了同一句判定口径）；v1.8 给邮件加「今日工程进展」板块并让主题可配：新增 `collector/repo_progress.py` 只读本地 Git 历史（当日提交数、迭代版本号、变更起点层级、规范链改动、闭环判定），规范覆盖率由 `main.py` 注入 callable（采集层不依赖治理层），邮件版式由四段合成五段，主题改由 `email.subject_pattern` 模板渲染（占位符 `{team_name}`/`{date}`/`{grade}`/`{owner}`，个人属性只写配置不写模板），见 `design.md` §3.4/§6.7(2)/§6.8；v1.7 把邮件版式换成青碧身份色并微调结构：标题区吸收原「信息块」、报告名不再拼「工作日报」后缀、成员分组改为整宽名条，颜色收进 `template.EMAIL_PALETTE` 白名单，见 `design.md` §6.7；v1.6 把邮件日报从 Markdown 转换换成卡片式可视化版式：新增 `template.render_email_html(report)`，`generator/formatter.py` 改调它生成 `report.html`，见 `design.md` §6.7；版式只在邮件侧，不引用 `webview/` 的 `ant` 令牌，红色 `--accent` 不用于邮件；v1.5.1 修掉提交条目在邮件 HTML 里被拍平、看串的呈现缺陷，见 `design.md` §3.3 第 7/8 条；v1.5 在 v1.4 基础上把日报里的提交信息收成「标题 + 正文前 3 句摘要」，见 `design.md` §3.3；v1.4 时取消默认的非工作日跳过：`main.run(working_days_only=False)`
  默认每天都能跑，周末/节假日照常采集→生成→推送；旧行为保留为显式 `--working-days-only`，
  起点在 `specs/proposal.md` §3.3，见 `specs/design.md` §6.6）
- 任务进度：Task 1 ~ Task 13 全部完成
- 测试：`python -m pytest` 全绿
- 本地演练：`python main.py --config config.yaml --date 2026-08-20 --mock`
- 健康检查：`python main.py --config config.yaml --check`
- 展示页：`python main.py --serve`（只读 `data/reports.db`，浏览器打开 http://127.0.0.1:8000/）

## 常用命令

```bash
python -m pytest                      # 全量回归测试
python main.py --mock --date 2026-08-20   # 端到端演示（不推送）
python main.py --check               # 健康检查
python main.py --serve               # 启动本地只读展示页（v1.2）
```
