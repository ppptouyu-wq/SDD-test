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
├── collector/        # 采集层：github / lark_task / lark_msg / lark_attendance
├── generator/        # 生成层：formatter / template
├── notifier/         # 推送层：email / lark_bot
├── shared/           # 共享层：config / logger / errors / storage / retry / calendar / models
├── webview/          # 展示层（v1.2）：views（纯逻辑）/ app（HTTP 薄壳）/ static（原生 HTML+CSS）
├── tests/            # 单元测试 + 集成测试
└── main.py           # 编排入口
```

## 当前状态

- 版本：v1.3（v1.2 基础上把飞书考勤接上：成员身份映射拆成 `lark`（open_id）+
  `lark_employee_id`（员工ID），并按 `specs/tasks.md` Task 6 的 v1.3 验收标准
  区分"今日无记录"与"考勤数据暂不可用"）
- 任务进度：Task 1 ~ Task 12 全部完成
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
