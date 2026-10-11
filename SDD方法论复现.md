# 《SDD 实战：规范驱动开发之道》复现

本仓库是对黄佳《SDD 实战：规范驱动开发之道》（人民邮电出版社，2026）的完整复现。
三个子项目各自独立、可单独运行；另有若干方法论产物。

> **本仓库不含任何凭据。** 飞书 `app_id`/`app_secret`、群 `chat_id`、QQ 邮箱授权码
> 全部没有，仓库里只有 `.example` 占位模板。想让邮件/飞书通知发出去，
> **要自己配**——四样东西（`.env` 里的两个飞书密钥与 `SMTP_PASSWORD`、
> `config.email.yaml` 里的邮箱参数）详见 `README.md` 的「本仓库不含任何凭据」与
> 「发邮件 / 推飞书需要自己配什么」两节。

> **运行结果输出在哪**：只有一个地方——`sdd-daily-report/data/reports.db`
> （SQLite，表 `daily_reports`，一天一条，含 markdown / html / payload 三份）。
> 终端那几行 JSON 是日志，不是结果；日报不生成 md / html 文件。
> `data/` 被 `.gitignore` 忽略，不会上传。详见 `README.md`「运行结果输出在哪」。
>
> **怎么看日报内容**：`python main.py --serve` 起本地只读网页（v1.2 迭代产物），
> 或用 `_show_report.py` 导出成 md / html 文件。详见 `README.md`「展示页」。

## 子项目

| 目录 | 内容 | 测试 |
|---|---|---|
| `sdd-daily-report/` | **主项目**「智能日报生成器」（第 4-7 章）。含 Agent 设计模式、团队实践与度量、适用性自评、Harness 审计、本地只读展示页（v1.2 迭代）、飞书双 ID 考勤接线（v1.3 迭代） | 347 通过 |
| `kb-search/` | **案例项目**「知识库语义搜索工具」（第 1-2 章） | 109 通过、1 跳过 |
| `brownfield-demo/` | **Brownfield 四步法**演示（第 7.4 节） | 50 通过 |

合计 **506 个测试**。一键运行：

```bash
python run_all_tests.py     # 需先安装 pytest
```

## 全书章节覆盖

| 章节 | 产物 |
|---|---|
| 第 1-2 章 | SDD 五大原则与六阶段工作流；`kb-search` 案例项目 |
| 第 3 章 | 工具链全景、四层生态、`docs/SDD开源框架对照.md`、`sdd_agents/framework.py`（图 3-7 决策树） |
| 第 4 章 | `specs/proposal.md` + `specs/contracts/api-spec.yaml` |
| 第 5 章 | `specs/design.md` + ADR-001/002/003/004 |
| 第 6 章 | `specs/tasks.md`（Task 1-12）+ 子智能体并行调度 |
| 第 7 章 | 三层测试、验证与迭代、v1.1 与 v1.2 两次需求变更迭代、Brownfield 补规范 |
| 第 8 章 | 生成—评审、层级委托、护栏三明治、条件路由；`sdd_agents/harness.py`（Harness 审计） |
| 第 9 章 | 三阶段/三角色/治理检查/度量；`team-templates/` |
| 第 10 章 | `sdd_agents/suitability.py`（SDD 适用性自评） |

## 真实链路验证情况

**已真实验证**：GitHub 采集、邮件推送（QQ SMTP）、飞书群推送、飞书群消息采集、
飞书任务/通讯录/群列表接口、展示页（`--serve` 的 HTTP 端到端）。

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
