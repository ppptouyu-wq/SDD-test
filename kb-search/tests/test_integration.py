"""specs/tasks.md Task 5 验收标准：端到端集成测试。

- 同步 3 篇文档后，检索能召回语义相关片段
- 单篇文档读取失败时其余文档正常入库，且 IngestReport 标注失败 doc_id
- 内容未变更的文档二次同步被跳过
- 检索结果携带正确的文档标题与链接
"""

from __future__ import annotations

from kb_search.demo_data import build_demo_stack, demo_documents
from kb_search.embeddings import LocalHashEmbedding
from kb_search.service import IngestService
from kb_search.sources import MockFeishuDocSource
from kb_search.vectorstore import InMemoryVectorStore


def test_end_to_end_sync_then_search(synced_stack):
    search, ingest, store = synced_stack

    assert store.count() > 0
    hits = search.search("接口限流怎么做", top_k=3)

    assert hits, "应召回结果"
    assert hits[0].doc_id == "doc-rate-limit"
    assert hits[0].title == "网关接口限流方案"
    assert hits[0].url == "https://feishu.cn/docx/doc-rate-limit"
    assert "限流" in hits[0].text


def test_semantic_relevance_beats_irrelevant_docs(synced_stack):
    """不同主题的查询应命中各自的文档，而不是随机召回。"""
    search, _, _ = synced_stack

    cases = {
        "接口限流怎么做": "doc-rate-limit",
        "发布之后怎么回滚": "doc-deploy",
        "日志和告警规范": "doc-observability",
    }
    for query, expected_doc in cases.items():
        hits = search.search(query, top_k=1)
        assert hits, f"{query!r} 无命中"
        assert hits[0].doc_id == expected_doc, (
            f"{query!r} 期望 {expected_doc}，实际 {hits[0].doc_id}"
        )


def test_relevance_score_is_meaningful(synced_stack):
    """相关查询的分数应显著高于无关查询 —— 证明向量确实携带语义信息。"""
    search, _, _ = synced_stack

    relevant = search.search("接口限流", top_k=1)[0].score
    irrelevant = search.search("报销流程", top_k=1)[0].score

    assert relevant > irrelevant
    assert relevant > 0.1


def test_multiple_chunks_of_same_doc_share_link():
    """proposal.md §3.1：同一文档的多个片段应可追溯到同一链接。"""
    from datetime import datetime, timezone
    from kb_search.models import Document

    long_doc = Document(
        doc_id="doc-long",
        title="长文档",
        url="https://feishu.cn/docx/doc-long",
        updated_at=datetime.now(tz=timezone.utc),
        content=("限流相关的内容。" * 40) + "\n\n" + ("发布相关的内容。" * 40),
    )
    source = MockFeishuDocSource([long_doc])
    embedding = LocalHashEmbedding()
    store = InMemoryVectorStore()
    ingest = IngestService(source, embedding, store, chunk_kwargs={"size": 200, "overlap": 40})

    report = ingest.sync()
    assert report.chunks_written >= 2

    hits = store.search(embedding.encode_one("限流"), top_k=10)
    doc_hits = [h for h in hits if h.doc_id == "doc-long"]
    assert len(doc_hits) >= 1
    assert {h.url for h in doc_hits} == {"https://feishu.cn/docx/doc-long"}


def test_failure_isolation_end_to_end():
    source = MockFeishuDocSource(demo_documents(), fail_for=["doc-observability"])
    embedding = LocalHashEmbedding()
    store = InMemoryVectorStore()
    ingest = IngestService(source, embedding, store)

    report = ingest.sync()

    assert report.failed_doc_ids == ["doc-observability"]
    assert report.succeeded == 2
    # 失败的文档不应留下任何片段
    assert "doc-observability" not in store.doc_ids()
    # 其余文档的检索不受影响
    hits = store.search(embedding.encode_one("接口限流"), top_k=3)
    assert hits and hits[0].doc_id == "doc-rate-limit"


def test_incremental_sync_end_to_end():
    source = MockFeishuDocSource(demo_documents())
    embedding = LocalHashEmbedding()
    store = InMemoryVectorStore()
    ingest = IngestService(source, embedding, store)

    first = ingest.sync()
    second = ingest.sync()

    assert first.chunks_written > 0
    assert second.skipped == first.total
    assert second.chunks_written == 0
    assert store.count() == first.chunks_written


def test_unsynced_query_returns_low_scores(synced_stack):
    """语料里没有的主题，不应出现高置信命中（避免"什么都召回"）。"""
    search, _, _ = synced_stack

    hits = search.search("员工报销标准", top_k=3)

    assert not hits or hits[0].score < 0.1
