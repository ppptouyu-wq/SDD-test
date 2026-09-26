# CLAUDE.md

> 本文件是 AI 的持久上下文锚点（第 3 章）：规范文件索引 + 核心约束。
> 与主项目 `sdd-daily-report/CLAUDE.md` 同构；本项目的工具配置由
> `sdd-daily-report/scripts/generate_tool_configs.py` 按各自来源生成。

## 项目规范

所有开发工作必须遵循以下规范文档：

- 需求规范：`specs/proposal.md`
- 架构设计：`specs/design.md`
- 任务清单：`specs/tasks.md`
- 接口契约：`specs/contracts/data-models.md`、`specs/contracts/api-spec.yaml`
- 架构决策：`specs/adrs/`（ADR-001 Qdrant、ADR-002 text-embedding-3-small）

**规范是第一手工件，代码是规范的衍生物。** 当代码与规范冲突时，改代码去符合规范。

## 开发规则

1. **不写规范外的功能**：`specs/proposal.md` § 2.2 明确排除了大模型总结与问答、前端界面、
   权限与用户体系、多租户隔离、文档写入 —— 一律不做。
2. **不改 `specs/` 下的文件**：规范变更必须由人类确认。若实现中发现规范缺陷，先提出修改建议。
3. **按 tasks.md 逐任务执行**：Task 1~5，一个任务 = 一个可验证的交付物。
4. **接口签名必须与 `specs/design.md` §4 一致**。
5. **同时生成实现与测试**：验收标准（`- [ ]` 清单）要逐条转成测试用例。
6. **禁止硬编码密钥**：飞书与 Embedding 凭据一律走环境变量
   （`LARK_APP_ID`、`LARK_APP_SECRET`、`OPENAI_API_KEY`）。
7. **保持模块边界**：`sources/` 不切分、不向量化；`embeddings/` 不检索；
   `vectorstore/` 不调飞书。
8. **外部依赖必须可替换**：飞书 / Embedding / 向量库三者都定义为 Protocol，
   各提供真实实现与本地实现 —— 否则领域逻辑无法被测试。

## 目录结构

```
kb-search/
├── specs/                  # 规范（第一手工件）
│   ├── proposal.md
│   ├── design.md
│   ├── tasks.md
│   ├── contracts/          # data-models.md、api-spec.yaml
│   └── adrs/
├── kb_search/
│   ├── models.py           # 数据模型与领域异常
│   ├── chunking.py         # 纯函数文本切分
│   ├── embeddings.py       # EmbeddingModel + 本地/OpenAI 实现
│   ├── vectorstore.py      # VectorStore + 内存/Qdrant 实现
│   ├── sources.py          # FeishuDocSource + Mock/HTTP 实现
│   ├── service.py          # SearchService / IngestService
│   ├── handlers.py         # ★ HTTP 处理逻辑（不依赖 Web 框架，可完整测试）
│   ├── api.py              # FastAPI 薄壳（只做适配）
│   └── demo_data.py        # 演示语料
└── tests/
```

## 当前状态

- 版本：v1.0（对应书中第 1-2 章的最小完整示例）
- 任务进度：Task 1 ~ Task 5 全部完成
- 测试：`python -m pytest` —— 88 通过、1 跳过（跳过的仅是 FastAPI 适配层）
- 本地演示：`python -m kb_search "接口限流" "发布回滚"`

## 常用命令

```bash
python -m pytest -q                    # 全部测试
python -m kb_search                    # 同步演示语料 + 示例查询
python -m kb_search "接口限流" --top-k 5
python -m kb_search --serve            # 启动 HTTP 服务（需 [service] 依赖）
```
