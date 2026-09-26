# 数据模型定义（data-models.md）

> 契约来源：`specs/design.md` §3，字段级展开。结构化版本见 `api-spec.yaml`。
> 与主项目 `sdd-daily-report/specs/contracts/data-models.md` 同构。

## 1. 输入模型

### Document（飞书文档）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| doc_id | str | 是 | 文档 ID |
| title | str | 是 | 标题 |
| url | str | 是 | 原文链接 |
| content | str | 是 | 正文纯文本；为空时不产生片段 |
| updated_at | datetime | 是 | 最后更新时间 |

## 2. 中间模型

### Chunk（片段）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| chunk_id | str | 是 | 形如 `<doc_id>#<序号>`，序号从 0 开始 |
| doc_id | str | 是 | 来源文档 ID |
| title | str | 是 | 来源文档标题（冗余存储，避免检索时回查） |
| url | str | 是 | 来源文档链接 |
| text | str | 是 | 片段正文，带重叠 |
| position | int | 是 | 在文档中的序号 |

**切分规则**：默认单片段 400 字符、重叠 80 字符；优先在段落/句子边界收尾，
避免把一句话劈开。`overlap` 必须满足 `0 <= overlap < size`。

## 3. 输出模型

### SearchHit（检索结果）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| chunk_id | str | 是 | |
| doc_id | str | 是 | |
| title | str | 是 | |
| url | str | 是 | 同一文档的多个片段共享同一 url（proposal.md §3.1） |
| text | str | 是 | 片段原文 |
| score | float | 是 | 余弦相似度，0~1，降序排列 |

### IngestItemResult（单篇同步结果）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| doc_id | str | 是 | |
| ok | bool | 是 | 是否处理成功 |
| chunks | int | 否 | 写入片段数，默认 0 |
| skipped | bool | 否 | 内容未变更而跳过，默认 False |
| error | str \| None | 否 | 失败原因；成功时为 None |

### IngestReport（同步汇总）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| total | int | 是 | 本次待处理文档数 |
| succeeded | int | 是 | 成功且实际写入的文档数 |
| failed | int | 是 | 失败文档数 |
| skipped | int | 是 | 因内容未变而跳过的文档数 |
| chunks_written | int | 是 | 本次写入片段总数 |
| items | list[IngestItemResult] | 是 | 每篇文档的明细 |
| failed_doc_ids | list[str] | 是 | 失败文档 ID（由 items 派生，便于调用方定位） |

## 4. 向量约定

| 项 | 值 | 来源 |
|---|---|---|
| 维度 | 1536 | ADR-002（text-embedding-3-small） |
| 归一化 | L2 归一化（模长为 1） | design.md §4 |
| 相似度 | 余弦相似度，0~1 | design.md §3 |
| 空文本 | 全零向量 | 实现约定，不抛异常 |

## 5. 错误处理约定（design.md §6.1）

| 场景 | 映射 |
|---|---|
| query 为空/全空白/超长 | `ValidationError` → HTTP 400 |
| 单篇文档读取失败 | 记入 `IngestItemResult.error`，不中断整批 |
| Embedding 失败 | 该文档标记失败，不写半截数据 |
| 向量库不可用 | `VectorStoreError` → HTTP 503 |
| 向量库为空 | 返回空列表（HTTP 200） |

**关键区分**：`get_content_hash()` 返回 `None` 表示"从未同步过"，
返回空串表示"同步过但内容为空" —— 两者不得混淆。
