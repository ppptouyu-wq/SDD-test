"""契约一致性测试：把 specs/contracts/api-spec.yaml 当作事实来源校验实现。

与主项目 `sdd-daily-report/tests/test_contracts.py` 同构。
目的同样是防止"规范写了 A、代码做成 B"的漂移。
"""

from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path

import pytest
import yaml

from kb_search import embeddings, handlers, models, service, sources, vectorstore

SPEC_PATH = Path(__file__).resolve().parent.parent / "specs" / "contracts" / "api-spec.yaml"


@pytest.fixture(scope="module")
def spec() -> dict:
    assert SPEC_PATH.exists(), f"缺少接口契约文件：{SPEC_PATH}"
    return yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 结构

def test_spec_is_valid_openapi_with_required_sections(spec):
    assert spec["openapi"].startswith("3.0")
    assert "schemas" in spec["components"]
    assert "x-module-contracts" in spec
    assert "x-contract-rules" in spec


def test_all_expected_paths_documented(spec):
    assert {"/search", "/ingest", "/healthz"} <= set(spec["paths"])


def test_all_expected_schemas_exist(spec):
    required = {
        "Document", "Chunk", "SearchHit", "SearchRequest", "SearchResponse",
        "IngestRequest", "IngestItemResult", "IngestReport", "HealthResponse",
    }
    assert required <= set(spec["components"]["schemas"])


# ------------------------------------------------- 契约字段 ↔ 数据类字段

@pytest.mark.parametrize(
    "schema_name, dataclass_name",
    [
        ("Document", "Document"),
        ("Chunk", "Chunk"),
        ("SearchHit", "SearchHit"),
        ("IngestItemResult", "IngestItemResult"),
        ("IngestReport", "IngestReport"),
    ],
)
def test_schema_properties_match_dataclass_fields(spec, schema_name, dataclass_name):
    """规范声明的字段集合必须与实现的数据类字段集合一致。"""
    declared = set(spec["components"]["schemas"][schema_name]["properties"])
    actual = {f.name for f in dataclasses.fields(getattr(models, dataclass_name))}
    # IngestReport.failed_doc_ids 是派生属性，契约里作为响应字段记录
    extra = {"failed_doc_ids"} if schema_name == "IngestReport" else set()

    assert declared - extra == actual, (
        f"{schema_name} 契约与实现不一致："
        f"契约多出={declared - extra - actual}，实现多出={actual - (declared - extra)}"
    )


def test_dimension_is_1536_in_spec_and_code(spec):
    """ADR-002：向量维度固定 1536，契约与实现必须一致。"""
    dim = spec["components"]["schemas"]["Chunk"]  # 仅确保 schema 可读
    assert dim is not None
    contract_dim = (
        spec["x-module-contracts"]["embeddings.EmbeddingModel"]["functions"][1]["returns"]["const"]
    )
    assert contract_dim == models.EMBEDDING_DIMENSION == 1536


def test_score_range_matches_documented_bounds(spec):
    score = spec["components"]["schemas"]["SearchHit"]["properties"]["score"]
    assert score["minimum"] == 0 and score["maximum"] == 1


def test_top_k_default_matches_code(spec):
    top_k = spec["components"]["schemas"]["SearchRequest"]["properties"]["top_k"]
    assert top_k["default"] == models.DEFAULT_TOP_K
    assert models.MAX_TOP_K == 100  # 契约描述里的上限


# --------------------------------------------- 契约函数 ↔ 实际签名

@pytest.mark.parametrize(
    "contract_key, obj, func_names",
    [
        ("sources.FeishuDocSource", sources.MockFeishuDocSource, ("list_documents", "fetch")),
        ("embeddings.EmbeddingModel", embeddings.LocalHashEmbedding, ("encode",)),
        (
            "vectorstore.VectorStore",
            vectorstore.InMemoryVectorStore,
            ("upsert", "search", "count", "get_content_hash"),
        ),
        ("service.SearchService", service.SearchService, ("search",)),
        ("service.IngestService", service.IngestService, ("sync",)),
        ("handlers", None, ("handle_search", "handle_ingest", "handle_healthz")),
    ],
)
def test_contract_declares_existing_functions(spec, contract_key, obj, func_names):
    contract = spec["x-module-contracts"][contract_key]
    declared = {f["name"] for f in contract["functions"]}

    assert set(func_names) <= declared, f"{contract_key} 契约缺少 {set(func_names) - declared}"

    for name in func_names:
        target = getattr(handlers, name, None) if obj is None else getattr(obj, name, None)
        assert callable(target), f"{contract_key} 声明的 {name}() 在实现中不存在"


def test_search_service_signature_matches_contract():
    signature = inspect.signature(service.SearchService.search)
    assert list(signature.parameters) == ["self", "query", "top_k"]


def test_ingest_service_signature_matches_contract():
    signature = inspect.signature(service.IngestService.sync)
    assert "doc_ids" in signature.parameters


# ------------------------------------------------------- 契约规则

def test_contract_rules_documented(spec):
    rules = spec["x-contract-rules"]
    assert len(rules) >= 5
    joined = " ".join(r["rule"] for r in rules)
    assert "静默返回空" in joined
    assert "可复现" in joined
    assert "二元组" in joined, "中文单字降权这条真实教训必须写进契约"


def test_documented_behaviors_are_actually_implemented():
    """契约里明确写下的行为，抽查关键的几条确实在代码里。"""
    source = (SPEC_PATH.parent.parent.parent / "kb_search" / "embeddings.py").read_text(
        encoding="utf-8"
    )
    assert "_UNIGRAM_WEIGHT" in source, "单字降权未实现，与契约规则 5 不符"

    service_src = (
        SPEC_PATH.parent.parent.parent / "kb_search" / "service.py"
    ).read_text(encoding="utf-8")
    assert "count() == 0" in service_src, "向量库为空时不应消耗 Embedding 调用"
