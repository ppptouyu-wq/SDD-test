# 知识库语义搜索工具 —— 任务清单

## 元信息
- 关联规范: specs/proposal.md、specs/design.md
- 任务总数: 5
- 预计总执行时间: 2到3h（包含测试）
- 执行策略: 按依赖关系顺序执行，独立任务可并行执行

---

## Task 1: 数据模型定义

描述: 定义 Document、Chunk、SearchHit、IngestReport 等数据模型与领域异常

输入: specs/design.md（数据模型定义）、specs/contracts/data-models.md
输出: kb_search/models.py、kb_search/errors.py
依赖: 无

验收标准:
- [ ] Document 包含 doc_id、title、url、content、updated_at 全部5个字段
- [ ] Chunk 包含 chunk_id、doc_id、title、url、text、position 全部6个字段
- [ ] SearchHit 包含 chunk_id、doc_id、title、url、text、score 全部6个字段
- [ ] IngestReport 含 total/succeeded/failed/skipped/chunks_written 统计字段
- [ ] 异常分四类：SourceError、EmbeddingError、VectorStoreError、ValidationError
- [ ] 单元测试全部通过

---

## Task 2: 飞书文档 API 对接

描述: 实现飞书文档读取接口，支持列出文档与按 ID 取正文，含 Token 刷新与失败标注

输入: design.md §4（FeishuDocSource 接口）、ADR-002（凭据走环境变量）
输出: kb_search/sources/feishu.py、tests/test_source.py
依赖: Task 1

验收标准:
- [ ] list_documents() 返回 doc_id 列表
- [ ] fetch(doc_id) 返回 Document，正文为纯文本
- [ ] 飞书 Token 过期时自动刷新后重试1次
- [ ] 单篇文档读取失败抛 SourceError 且消息含 doc_id
- [ ] 未配置凭据时抛出明确错误，不静默返回空
- [ ] 使用 Mock 数据的单元测试全部通过

---

## Task 3: 向量化与存储

描述: 实现 Embedding 接口与向量库接口；提供本地实现以便无凭据验证，并提供文本切分

输入: design.md §3（Chunk 模型）、§4（EmbeddingModel / VectorStore 接口）、ADR-001
输出: kb_search/embeddings/、kb_search/vectorstore/、kb_search/chunking.py、tests/test_vectorstore.py
依赖: Task 1

验收标准:
- [ ] LocalHashEmbedding 输出维度恒为 1536 且向量已 L2 归一化
- [ ] 相同文本编码结果完全一致（确定性）
- [ ] chunk_text() 按长度切分且保留重叠，chunk_id 形如 doc_id#序号
- [ ] 空文本不产生任何片段（proposal.md §3.3）
- [ ] InMemoryVectorStore 支持 upsert / search / count / get_content_hash
- [ ] search() 结果按余弦相似度降序，且携带来源 doc_id 与标题
- [ ] 使用 Mock 数据的单元测试全部通过

---

## Task 4: 搜索 API 实现

描述: 实现 SearchService 与 IngestService，并暴露 POST /search 与 POST /ingest

输入: design.md §4（服务层与 HTTP 契约）、§6.1（错误处理）
输出: kb_search/service.py、kb_search/api.py、tests/test_service.py、tests/test_api.py
依赖: Task 2、Task 3

验收标准:
- [ ] POST /search 接受 query 与 top_k，返回不超过 top_k 条结果
- [ ] 返回结果按相似度降序，且包含相似度分数
- [ ] query 为空或全空白时返回 400
- [ ] top_k > 100 时按 100 截断，< 1 时按 1 处理
- [ ] 向量库为空时返回空结果列表，不抛异常
- [ ] 检索到同一文档的多个片段时可追溯到同一文档链接
- [ ] 单元测试全部通过

---

## Task 5: 集成测试

描述: 端到端验证"同步 → 检索"链路，并覆盖降级场景

输入: proposal.md §3 验收标准、design.md §6.1 错误处理策略
输出: tests/test_integration.py
依赖: Task 4

验收标准:
- [ ] 同步 3 篇文档后，检索能召回语义相关片段
- [ ] 单篇文档读取失败时其余文档正常入库，且 IngestReport 标注失败 doc_id
- [ ] 内容未变更的文档二次同步被跳过（IngestReport.skipped > 0）
- [ ] 检索结果携带正确的文档标题与链接
- [ ] 端到端测试全部通过

---

## 执行顺序与依赖关系

```
Task 1 ──┬──▶ Task 2 ──┐
         └──▶ Task 3 ──┴──▶ Task 4 ──▶ Task 5
```

- **关键路径**：Task 1 → Task 3 → Task 4 → Task 5
- **最大并行度**：2（Task 2 与 Task 3 可同时执行）
