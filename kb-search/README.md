# 知识库语义搜索工具（kb-search）

> 复现自《SDD 实战：规范驱动开发之道》**第 1-2 章的案例项目**。
> 与主项目 `sdd-daily-report` 共用同一套 SDD 方法论：先写 `proposal.md` → `design.md` → `tasks.md`，再实现并逐条验收。

## 它做什么

团队知识散落在飞书文档里，关键词检索搜不到同义表述。本工具做**语义检索**：
用自然语言查询，召回语义相关的文档片段，并返回文档标题与链接。

- **文档接入**：从飞书读取文档 → 切分为带重叠的片段 → 向量化 → 写入向量库
- **语义检索**：查询向量化 → 近邻检索 → 返回 Top-K 片段与相似度分数
- **增量同步**：以内容哈希判断变更，未变更的文档直接跳过

## 快速开始（无需任何外部凭据）

```bash
cd kb-search
python -m kb_search                      # 同步演示语料，再跑几个示例查询
python -m kb_search "接口限流" "发布回滚"   # 指定查询词
python -m kb_search --top-k 5 "日志规范"
python -m pytest -q                      # 全部测试
```

实测输出（`python -m kb_search "接口限流怎么做" "员工报销标准"`）：

```
查询：'接口限流怎么做' → 命中 2 条
  1. [0.2090] 网关接口限流方案 — 背景：核心接口在大促期间被上游重试打爆…
  2. [0.0008] 发布与回滚流程 …

查询：'员工报销标准' → 命中 3 条          （语料里没有这个主题）
  1. [0.0058] 可观测性规范 …
```

相关查询 0.21、无关查询 0.006 —— 相差约 35 倍，说明向量确实携带判别信息。

## 架构

```
前端(已有) ──HTTP─▶ api.py (FastAPI 薄壳)
                        │
                   service.py（SearchService / IngestService）
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
   sources.py     embeddings.py    vectorstore.py
   ├ HttpFeishu   ├ OpenAiEmbed    ├ QdrantStore
   └ MockFeishu   └ LocalHashEmbed └ InMemoryStore
      （真实）        （可替换）        （可替换）
```

| 模块 | 职责 | 不负责 |
|---|---|---|
| `api.py` | HTTP 校验、错误码映射 | 不做业务逻辑、不直接调外部 API |
| `service.py` | 编排检索与同步、决定切分与增量 | 不做 HTTP 校验、不实现向量化与存储 |
| `sources.py` | 读飞书文档 | 不切分、不向量化、不写库 |
| `embeddings.py` | 文本 → 向量 | 不检索、不管理存储 |
| `vectorstore.py` | 向量写入与近邻检索 | 不切分、不调飞书 |
| `chunking.py` | 纯函数文本切分 | 不涉及 IO |

## 为什么外部依赖都做成"可替换"

书中该案例用于**演示六阶段工作流**，未给出完整实现。本复现的核心工程决策是：
**飞书 / Embedding / 向量库三者都定义为 Protocol，各提供一个真实实现和一个本地实现。**

这不是偷懒，而是为了让**领域逻辑（切分、增量、排序、降级）能被真实断言**——
否则整条链路都要外部凭据，等于无法验证。具体做法：

- `LocalHashEmbedding` 用**中文二元组哈希 + L2 归一化**产生向量，
  使余弦相似度真实反映字面重叠，因此可以断言"查'接口限流'应召回限流文档"这类语义行为，
  而不是只断言"没有抛异常"。
- 接入真实模型只需替换实现（`OpenAiEmbedding`），领域层代码不变。

> 实现过程中这里还暴露过一个真实教训：最初单字与二元组同权，
> 导致无关查询"员工报销标准"也能拿到 0.11 分（假阳性）。
> 把单字权重降到 0.15 后，无关查询降到 0.006，相关查询仍为 0.21。

## 规范（第一手工件）

| 文件 | 回答的问题 |
|---|---|
| `specs/proposal.md` | 做什么 / 不做什么 / 怎么验收 |
| `specs/design.md` | 分层架构、模块职责、数据模型、接口契约、非功能约束 |
| `specs/tasks.md` | Task 1~5，每个任务含输入/输出/依赖/验收标准 |
| `specs/contracts/data-models.md` | 字段级契约 |
| `specs/contracts/api-spec.yaml` | HTTP 契约（OpenAPI 3.0.3） |
| `specs/adrs/` | ADR-001 Qdrant、ADR-002 text-embedding-3-small |

## 任务进度

| # | 任务 | 状态 |
|---|---|---|
| 1 | 数据模型定义 | ✅ |
| 2 | 飞书文档 API 对接 | ✅（Mock 全测；真实实现需凭据） |
| 3 | 向量化与存储 | ✅ |
| 4 | 搜索 API 实现 | ✅ |
| 5 | 集成测试 | ✅ |

## 接真实依赖

```bash
export LARK_APP_ID=...          # 飞书应用（文档读取权限）
export LARK_APP_SECRET=...
export OPENAI_API_KEY=...       # Embedding 服务
# 另需一个可达的 Qdrant：docker run -p 6333:6333 qdrant/qdrant
```

不接真实依赖时的行为：`HttpFeishuDocSource` 与 `OpenAiEmbedding` 会在**调用时**
抛出带原因的领域异常，而不是静默返回空 —— 静默返回空会让同步任务"成功但什么都没做"。

## 与主项目的关系

两个项目共用同一套方法论与工具（`sdd_agents` 的生成—评审、条件路由等可直接用于
本项目：`load_tasks()` 与 `router.build_route_table()` 支持传入本项目的
`specs/tasks.md` 与 `specs/design.md`）。

本项目**不包含**（`proposal.md` §2.2 明确排除）：大模型总结与问答、前端界面、
权限与用户体系、多租户隔离、文档写入。
