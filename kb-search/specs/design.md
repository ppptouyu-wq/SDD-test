# 知识库语义搜索工具 — 架构设计

## 1. 系统架构

- 分层架构：接入层（HTTP）→ 领域服务层 → 外部适配层（飞书 / Embedding / 向量库）

```
                     ┌─────────────────────────────────────────┐
   前端(已有) ──HTTP─▶│  接入层 api.py (FastAPI)                │
                     │  POST /search   POST /ingest             │
                     └─────────────────────────────────────────┘
                                        │
                     ┌─────────────────────────────────────────┐
                     │  领域服务层 service.py                   │
                     │  SearchService     IngestService         │
                     └─────────────────────────────────────────┘
                          │               │              │
              ┌───────────┘               │              └───────────┐
              ▼                           ▼                          ▼
   ┌────────────────────┐   ┌────────────────────┐   ┌────────────────────┐
   │ sources/           │   │ embeddings/        │   │ vectorstore/       │
   │ FeishuDocSource    │   │ EmbeddingModel     │   │ VectorStore        │
   │  └ HttpFeishu      │   │  ├ OpenAiEmbedding │   │  ├ QdrantStore     │
   │  └ FakeFeishu      │   │  └ LocalHashEmbed  │   │  └ InMemoryStore   │
   └────────────────────┘   └────────────────────┘   └────────────────────┘
                                  （真实外部依赖）        （真实外部依赖）
```

**为什么是分层而不是微服务**：本期只有检索与同步两条流程，
`proposal.md` §2.2 已排除多租户与前端，引入微服务只会增加部署与调试成本。
与主项目同理，选择满足需求的最简架构。

## 2. 模块职责

| 模块 | 职责 | 不负责 |
|---|---|---|
| `kb_search/api.py` | HTTP 接入：参数校验、错误码映射、调用服务层 | 不做业务逻辑；不直接访问外部 API |
| `kb_search/service.py` | 编排检索与同步两条流程；决定 chunk 切分与增量策略 | 不做 HTTP 校验；不实现具体的向量化与存储 |
| `kb_search/sources/` | 从飞书读取文档列表与正文 | 不切分；不向量化；不写库 |
| `kb_search/embeddings/` | 把文本编码为定长向量 | 不做检索；不管理存储 |
| `kb_search/vectorstore/` | 向量写入与近邻检索 | 不做切分；不调用飞书 |
| `kb_search/models.py` | 跨模块的数据模型与异常 | 不含业务规则 |
| `kb_search/chunking.py` | 纯函数：文本切分为片段（带重叠） | 不涉及 IO |
| `kb_search/config.py` | 配置读取与校验（含密钥环境变量注入） | — |

> **"不负责"列的作用**（第 5 章 5.2.3）：若缺少负向约束，AI 容易把切分逻辑
> 写进 `sources/`、把飞书调用写进 `service.py`，导致边界模糊。

## 3. 数据模型

只承载语义，不绑定实现。

```
Document（飞书文档）
  doc_id: str            # 文档 ID
  title: str             # 标题
  url: str               # 原文链接
  content: str           # 正文纯文本
  updated_at: datetime   # 最后更新时间

Chunk（片段）
  chunk_id: str          # 片段 ID（doc_id + 序号）
  doc_id: str            # 来源文档 ID
  title: str             # 来源文档标题
  url: str               # 来源文档链接
  text: str              # 片段正文
  position: int          # 在文档中的序号（从 0 开始）

SearchHit（检索结果）
  chunk_id: str
  doc_id: str
  title: str
  url: str
  text: str
  score: float           # 余弦相似度，0~1

IngestItemResult（单篇文档同步结果）
  doc_id: str
  ok: bool
  chunks: int            # 写入片段数
  skipped: bool          # 增量同步时内容未变而跳过
  error: str | None      # 失败原因

IngestReport（一次同步的汇总）
  total: int
  succeeded: int
  failed: int
  skipped: int
  chunks_written: int
  items: list[IngestItemResult]
```

**向量维度固定为 1536**（ADR-002），作为契约的一部分：更换模型必须同步评估迁移。

## 4. 接口契约

```python
# 接入层（HTTP，见 specs/contracts/api-spec.yaml）
POST /search   {query: str, top_k: int = 5}  -> SearchResponse
POST /ingest   {doc_ids: list[str] | None}   -> IngestReport

# 领域服务层
SearchService.search(query: str, top_k: int = 5) -> list[SearchHit]
IngestService.sync(doc_ids: list[str] | None = None) -> IngestReport

# 外部适配层
FeishuDocSource.list_documents() -> list[str]            # 返回 doc_id
FeishuDocSource.fetch(doc_id: str) -> Document
EmbeddingModel.encode(texts: list[str]) -> list[list[float]]
EmbeddingModel.dimension -> int
VectorStore.upsert(chunks: list[Chunk], vectors: list[list[float]]) -> int
VectorStore.search(query_vector: list[float], top_k: int) -> list[SearchHit]
VectorStore.count() -> int
VectorStore.get_content_hash(doc_id: str) -> str | None
```

字段级契约见 `specs/contracts/data-models.md`；HTTP 契约见 `specs/contracts/api-spec.yaml`。

## 5. 技术选型（ADR）

- ADR-001：向量数据库 → Qdrant
- ADR-002：向量化模型 → text-embedding-3-small（1536 维）

## 6. 非功能性约束

### 6.1 错误处理：优雅降级

| 场景 | 处理方式 |
|---|---|
| 单篇文档读取失败 | 跳过该文档，在 IngestReport 中记录 doc_id 与原因，其余照常 |
| Embedding 服务超时 | 重试 2 次；仍失败则该批文档标记失败，不写半截数据 |
| 向量库不可用 | 检索直接返回 503；同步任务失败并告警 |
| query 为空 | 返回 400，不消耗 Embedding 调用 |
| 向量库为空 | 返回空结果列表，不抛异常（proposal.md §3.3） |
| top_k 越界 | > 100 按 100 截断；< 1 按 1 处理 |

关键原则：
1. 同步的失败不应影响已入库数据（先校验、再批量写）
2. 任何失败都必须有日志记录，且带上 doc_id
3. 检索路径不得因为单条脏数据而整体失败

### 6.2 安全性

```
1. 密钥管理
   - 飞书 app_secret 与 Embedding API Key 均通过环境变量注入
   - 严禁在代码或配置文件中硬编码
2. 数据边界
   - 只读取配置中列出的飞书文档目录
   - 检索结果只返回原文片段，不做二次加工
3. 输入校验
   - query 长度上限 2000 字符，超出返回 400
```

### 6.3 可运维性

```
1. 日志：JSON lines；每次检索记录 query 摘要、top_k、命中数与耗时
2. 健康检查：GET /healthz 返回向量库连通性与已入库片段数
3. 增量同步：以文档内容哈希判断是否变更，未变更则跳过（IngestReport.skipped）
```

## 7. 本复现的适配说明

书中该案例用于**演示六阶段工作流**，未给出完整实现。本复现做了两处工程决策：

1. **外部依赖抽象化**：飞书、Embedding、向量库三者都定义为 Protocol，
   各提供一个"真实实现"与一个"本地实现"。这样做不是为了偷懒，而是为了让
   **领域逻辑（切分、增量、排序、降级）能被真实断言测试** —— 否则整条链路
   都需要外部凭据，等于无法验证。
2. **本地向量用词法哈希**：`LocalHashEmbedding` 用 n-gram 哈希 + L2 归一化产生向量，
   使余弦相似度**真实反映字面重叠**。因此测试可以断言"查'接口限流'应召回含'限流'
   的片段"这类语义行为，而不是只断言"没有抛异常"。接入真实模型只需替换实现，
   领域层代码不变。
