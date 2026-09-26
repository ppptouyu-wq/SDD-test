"""specs/tasks.md Task 3 验收标准：向量化、切分、向量库。

- LocalHashEmbedding 输出维度恒为 1536 且向量已 L2 归一化
- 相同文本编码结果完全一致（确定性）
- chunk_text() 按长度切分且保留重叠，chunk_id 形如 doc_id#序号
- 空文本不产生任何片段
- InMemoryVectorStore 支持 upsert / search / count / get_content_hash
- search() 结果按余弦相似度降序，且携带来源 doc_id 与标题
"""

from __future__ import annotations

import math

import pytest

from kb_search.chunking import chunk_text
from kb_search.embeddings import LocalHashEmbedding
from kb_search.models import EMBEDDING_DIMENSION, Chunk, VectorStoreError
from kb_search.vectorstore import InMemoryVectorStore, content_hash, cosine_similarity


# ============================================================ 向量化

def test_embedding_dimension_is_1536():
    model = LocalHashEmbedding()
    assert model.dimension == EMBEDDING_DIMENSION == 1536
    vector = model.encode_one("网关接口限流方案")
    assert len(vector) == 1536


def test_embedding_is_l2_normalized():
    model = LocalHashEmbedding()
    vector = model.encode_one("网关接口限流方案")
    norm = math.sqrt(sum(v * v for v in vector))
    assert norm == pytest.approx(1.0, abs=1e-9)


def test_embedding_is_deterministic():
    model = LocalHashEmbedding()
    a = model.encode_one("同一段文本")
    b = model.encode_one("同一段文本")
    assert a == b


def test_different_text_gives_different_vector():
    model = LocalHashEmbedding()
    a = model.encode_one("网关限流")
    b = model.encode_one("发布回滚")
    assert a != b
    assert cosine_similarity(a, b) < 1.0


def test_similar_text_scores_higher_than_unrelated():
    """本地向量的核心价值：余弦相似度必须真实反映字面重叠。"""
    model = LocalHashEmbedding()
    query = model.encode_one("接口限流")
    related = model.encode_one("网关层对接口做限流，超出后返回 429")
    unrelated = model.encode_one("发布窗口是工作日 14:00-17:00")

    assert cosine_similarity(query, related) > cosine_similarity(query, unrelated)


def test_empty_text_gives_zero_vector():
    model = LocalHashEmbedding()
    vector = model.encode_one("   ")
    assert all(v == 0.0 for v in vector)


def test_encode_rejects_bare_string():
    model = LocalHashEmbedding()
    with pytest.raises(TypeError):
        model.encode("不是序列")  # type: ignore[arg-type]


# ============================================================== 切分

def test_chunk_text_splits_and_keeps_overlap():
    text = "A" * 1000
    chunks = chunk_text(text, doc_id="doc1", size=400, overlap=80)

    assert len(chunks) >= 3
    assert chunks[0].chunk_id == "doc1#0"
    assert chunks[1].chunk_id == "doc1#1"
    assert [c.position for c in chunks] == list(range(len(chunks)))
    # 相邻片段应有重叠
    assert chunks[0].text[-40:] in chunks[1].text or chunks[1].text[:40] in chunks[0].text


def test_chunk_text_empty_returns_nothing():
    """proposal.md §3.3：飞书文档正文为空时不写入空片段。"""
    assert chunk_text("", doc_id="d") == []
    assert chunk_text("   \n\t ", doc_id="d") == []


def test_chunk_text_short_text_is_single_chunk():
    chunks = chunk_text("很短的一句话", doc_id="d", title="T", url="U")
    assert len(chunks) == 1
    assert chunks[0].title == "T"
    assert chunks[0].url == "U"
    assert chunks[0].text == "很短的一句话"


def test_chunk_text_prefers_paragraph_boundary():
    text = "第一段内容。\n\n第二段内容。\n\n第三段内容。"
    chunks = chunk_text(text, doc_id="d", size=12, overlap=2)
    # 切分点应尽量落在段落边界，而不是把句子劈开
    assert all(c.text.endswith(("。", "\n")) or len(c.text) < 12 for c in chunks)


def test_chunk_text_invalid_params():
    with pytest.raises(ValueError):
        chunk_text("abc", doc_id="d", size=0)
    with pytest.raises(ValueError):
        chunk_text("abc", doc_id="d", size=10, overlap=10)


# ========================================================== 向量库

def _chunk(cid: str, doc: str, text: str, position: int = 0) -> Chunk:
    return Chunk(
        chunk_id=cid, doc_id=doc, title=f"标题-{doc}", url=f"https://x/{doc}",
        text=text, position=position,
    )


def test_store_upsert_and_count():
    store = InMemoryVectorStore()
    model = LocalHashEmbedding()
    chunks = [_chunk("d1#0", "d1", "网关限流"), _chunk("d1#1", "d1", "发布流程", 1)]

    written = store.upsert(chunks, model.encode([c.text for c in chunks]))

    assert written == 2
    assert store.count() == 2


def test_store_search_returns_sorted_hits_with_metadata():
    store = InMemoryVectorStore()
    model = LocalHashEmbedding()
    chunks = [
        _chunk("d1#0", "d1", "在网关层对单租户做令牌桶限流，超出返回 429"),
        _chunk("d2#0", "d2", "发布窗口是工作日 14:00-17:00，禁止周五发布"),
    ]
    store.upsert(chunks, model.encode([c.text for c in chunks]))

    hits = store.search(model.encode_one("接口限流"), top_k=2)

    assert hits, "应至少命中一条"
    assert hits[0].doc_id == "d1"
    assert hits[0].title == "标题-d1"
    assert hits[0].url == "https://x/d1"
    assert hits[0].score >= hits[-1].score  # 降序
    assert all(isinstance(h.score, float) for h in hits)


def test_store_search_respects_top_k():
    store = InMemoryVectorStore()
    model = LocalHashEmbedding()
    chunks = [_chunk(f"d{i}#0", f"d{i}", f"限流方案第{i}版") for i in range(5)]
    store.upsert(chunks, model.encode([c.text for c in chunks]))

    assert len(store.search(model.encode_one("限流"), top_k=2)) == 2


def test_store_search_empty_returns_empty_list():
    """proposal.md §3.3：向量库为空时返回空结果列表，而不是抛异常。"""
    store = InMemoryVectorStore()
    assert store.search(LocalHashEmbedding().encode_one("任意"), top_k=5) == []


def test_store_reupsert_same_doc_replaces_old_chunks():
    """同一文档重新同步不应产生重复片段。"""
    store = InMemoryVectorStore()
    model = LocalHashEmbedding()
    old = [_chunk("d1#0", "d1", "旧内容"), _chunk("d1#1", "d1", "旧内容二", 1)]
    store.upsert(old, model.encode([c.text for c in old]))
    assert store.count() == 2

    new = [_chunk("d1#0", "d1", "新内容")]
    store.upsert(new, model.encode([c.text for c in new]))

    assert store.count() == 1
    assert store.search(model.encode_one("新内容"), 1)[0].text == "新内容"


def test_store_content_hash_roundtrip():
    store = InMemoryVectorStore()
    assert store.get_content_hash("d1") is None

    store.set_content_hash("d1", "正文内容")

    assert store.get_content_hash("d1") == content_hash("正文内容")
    assert content_hash("正文内容") != content_hash("改过的正文")


def test_store_upsert_length_mismatch_raises():
    store = InMemoryVectorStore()
    with pytest.raises(VectorStoreError):
        store.upsert([_chunk("d#0", "d", "x")], [])


def test_cosine_similarity_dimension_mismatch_raises():
    with pytest.raises(VectorStoreError):
        cosine_similarity([1.0, 2.0], [1.0])
