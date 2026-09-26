# SDD 开源框架对照（第 3 章 3.1）

> 复现《SDD 实战：规范驱动开发之道》第 3 章 3.1「三大 SDD 开源框架」。
> 表 3-1「五大框架对比」与各框架的命令清单均为书中原文转录。
>
> **为什么需要这份文档**：本项目实现了属于自己的 `specs/` + Markdown 流程，
> 但书中这一节的重点是"已有生态怎么用"。只做自己的实现而不看生态，
> 等于漏掉了这一章的一半。本文档补齐这一层认知，并给出等价映射。

## 一、五大框架对比（书中表 3-1，原文转录）

| 对比维度 | OpenSpec | Spec-Kit | BMAD | Superpowers | Kiro |
|---|---|---|---|---|---|
| **开发者** | Fission AI 团队 | GitHub | BMAD Code 公司 | Jesse Vincent | Amazon 旗下业务产品 |
| **核心哲学** | 轻量迭代，工具无关 | 规范即主工件，治理优先 | AI 即专家协作者 | 坚持 TDD，强调工程纪律优于灵感 | IDE 原生集成，实现从 Spec 到代码的端到端自动化 |
| **切入点／角度** | 斜杠命令 + 文档模板 | CLI + 斜杠命令 | 多智能体编排 | Skills 组合 + 强调前置测试 | IDE 内置 Spec 流水线 |
| **规范格式** | 标准 Markdown | Markdown + Constitution（宪法） | Markdown + 角色指令 | Markdown + Skills 模板 | Markdown + IDE 内置模板 |
| **AI 工具兼容** | 20 余种工具 | 18 种工具 | Claude Code／Cursor | 原生支持 Claude Code | 自研 IDE |
| **存量系统支持** | 强（Brownfield-first） | 强（三种场景） | 中等 | 强（适合补充测试覆盖率） | 中等 |
| **安装方式** | npm install -g | uv tool install（Python） | npx bmad-method install | 安装 Claude Code 插件 | 下载 Kiro IDE |
| **学习成本** | 低 | 中 | 高（12 种角色，34 种工作流） | 较低 | 较高（需迁移至 Kiro IDE） |
| **适合场景／团队规模** | 个人开发者／小型团队，1~3 人 | 企业级项目，需要治理和审计，1~10 人 | 复杂项目／多 Agent 协作，3~10 人 | 个人／小团队（TDD 派），1~5 人 | 中大型企业项目，10 人以上 |

**书中对此表的总结**（印刷 p.50）：

> "从 OpenSpec 到 Spec-Kit 再到 BMAD，自动化程度越来越高，
> 但学习成本和复杂度也越来越高。这就是工具选择的根本权衡。
> **没有银弹。** ……关键不在于你使用什么工具，而在于你有没有在编写代码前先撰写规范。
> **工具是加速器，不是方法论本身。**"

## 二、四层工具生态（3.1.4，原文转录）

| 层 | 定位 | 代表 |
|---|---|---|
| 第一层 原子技能 | 将"追问需求""执行 TDD""代码审查"等**单个动作**标准化 | Superpowers 的技能、grill-me |
| 第二层 上下文持久化 | 解决上下文漂移，把项目规则沉淀到仓库 | CLAUDE.md、AGENTS.md、Kiro Steering、Trellis |
| 第三层 规范生成框架 | 把模糊想法整理为 `requirements → design → tasks` | OpenSpec、Spec-Kit、Kiro Specs |
| 第四层 完整方法论 | 把头脑风暴→澄清→规划→执行→评审→复盘串成闭环 | BMAD、Superpowers、Compound Engineering |

> 书中提醒：这 4 层**不是互斥选项**，而是可组合的能力谱系。
> 选型不要问"哪个最流行"，而要问"**当前瓶颈在哪一层**"。

## 三、本项目落在生态的哪一层

诚实定位：本项目**没有使用任何框架**，属于书中所说的最简方案 ——
"在项目根目录建一个 `specs/` 文件夹，用 Markdown 编写需求和设计文档，
再将 `CLAUDE.md` 指向该目录"，这也是书中给初学者的建议。

| 层 | 本项目的对应实现 | 是否具备 |
|---|---|---|
| 原子技能 | 无（未实现 Skills 形式的原子技能） | ✗ |
| 上下文持久化 | `CLAUDE.md` + 四工具等价配置 + `.claude/settings.json` Hooks | ✓ |
| 规范生成框架 | **自建** `specs/` 三份规范 + `api-spec.yaml` 契约 | ✓（自建而非框架） |
| 完整方法论 | `sdd_agents/`（生成—评审、层级委托、护栏、条件路由、度量、治理） | 部分 |

## 四、用本项目走一遍各框架的命令

以「智能日报生成器」为例，看同样一件事在不同框架里怎么表达。
**本项目的流程与框架的命令是一一对应的** —— 说明书中的方法论是相通的。

### OpenSpec（文档驱动，10 步）

```
openspec init                       ← 初始化，生成标准目录结构
/opsx:new                           ← 或在 AI 工具中直接开始
/opsx:propose                       ← 创建结构化变更提案
/opsx:continue                      ← 填写 proposal.md
                                    ← 创建 specs/ 并编写 design.md
                                    ← 分解 tasks.md
/opsx:apply                         ← AI 按 tasks.md 逐步实现
/opsx:ff                            ← 快进：跳过已完成的任务
/opsx:verify                        ← 检查实现是否匹配规范
/opsx:archive                       ← 归档已完成的提案
```

**映射到本项目**：

| OpenSpec 命令 | 本项目等价操作 |
|---|---|
| `openspec init` | 建 `specs/` 目录 + 三份规范 |
| `/opsx:propose` + `/opsx:continue` | 写 `specs/proposal.md`（第 4 章） |
| （创建 specs/ 与 design.md） | 写 `specs/design.md` + `specs/contracts/`（第 5 章） |
| （分解 tasks.md） | 写 `specs/tasks.md`（第 6 章） |
| `/opsx:apply` | 按 Task 逐个执行，产出代码 + 测试 |
| `/opsx:ff` | DAG 分波调度中跳过已完成任务（`orchestrator.build_waves`） |
| `/opsx:verify` | `python -m pytest` + `python -m sdd_agents review` |
| `/opsx:archive` | Git commit（规范与代码同步提交） |

> OpenSpec 的"流动而非僵化"特别适合 **Brownfield** —— 可从单个提案切入、
> 边实现边补规范。这正是本项目 `brownfield-demo/` 演示的场景（第 7.4 节）。

### Spec-Kit（治理优先，8 个命令）

```
uv tool install specify-cli
specify init my-project --ai claude

/speckit.constitution   ← 建立项目治理原则（宪法）
/speckit.specify        ← 定义需求和用户故事
/speckit.plan           ← 创建技术实现方案
/speckit.tasks          ← 生成可执行的任务清单
/speckit.implement      ← 按任务列表逐步实现
/speckit.clarify        ← 澄清规范中的模糊之处（结构化问答）
/speckit.analyze        ← 跨文档一致性检查（规范与实现对齐）
/speckit.checklist      ← 生成自定义验证清单（合规性审计）
```

**映射到本项目**：

| Spec-Kit 命令 | 本项目等价实现 |
|---|---|
| `/speckit.constitution` | `CLAUDE.md` 的核心约束 + `notifier`/`collector` 的"不负责"边界 |
| `/speckit.specify` | `specs/proposal.md` |
| `/speckit.plan` | `specs/design.md` + ADR |
| `/speckit.tasks` | `specs/tasks.md` |
| `/speckit.implement` | 逐任务执行 |
| `/speckit.clarify` | 生成—评审模式的追问（`generate_review` 的检查清单） |
| **`/speckit.analyze`** | **`tests/test_contracts.py`（契约一致性测试）+ `sdd_agents review`** |
| `/speckit.checklist` | `specs/tasks.md` 的验收标准清单 |

> 书中评价：`/speckit.analyze` 的跨文档一致性检查"对需要严格审计追踪、
> 合规要求及变更管理的企业级项目至关重要"。
> **本项目用 `tests/test_contracts.py` 实现了同类能力**：
> 它把 `api-spec.yaml` 当事实来源，校验实现的字段与签名是否漂移 ——
> 实测抓到过 `data-models.md` 漏写"签退缺失"枚举。

### BMAD（角色驱动，多智能体编排）

BMAD 用 12 种角色、34 种工作流按"规划 → 开发 → 验证"三阶段编排多个 Agent。

**映射到本项目**：`sdd_agents/orchestrator.py` 的层级委托模式实现了同类结构 ——
管理者按 `tasks.md` 的 DAG 分波，把任务委派给工作者，只是角色由规范而非预设人设定义。

## 五、结论：本项目为什么不引入框架

书中的建议是明确的（印刷 p.64）：

> "如果你刚开始接触 SDD，不要在工具选择上花费过多精力。最简方案 ——
> 在项目根目录下构建一个 `specs/` 文件夹，用 Markdown 编写需求和设计文档，
> 再将 `CLAUDE.md` 指向该目录 —— 便足够起步。等你完成两三个项目、
> 积累了实践经验之后，再考虑是否引入 OpenSpec 或 Spec-Kit 来标准化流程。"

本项目是**单个复现项目**，规模落在 OpenSpec 的适用区间（1~3 人），
且需要把方法论本身展示清楚而不是被框架封装掉，因此选择自建 `specs/` 流程。

**若将来要引入框架**，按书中决策树（见 `sdd_agents/framework.py`）：

- 需要 CI/CD 自动化与审计 → **Spec-Kit**
- 2~5 人、无需重度治理 → **OpenSpec**
- 5 人以上、多 Agent 协作 → **BMAD**

## 六、参考链接

- Superpowers 的技能与实践：https://kage-ai.com/sdd/#ch3-superpowers
- 书中配套站点：https://kage-ai.com/sdd/
