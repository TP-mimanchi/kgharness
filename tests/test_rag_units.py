from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import HumanMessage
from llama_index.core.schema import NodeWithScore, TextNode

from app.rag.embeddings import lexicalize
from app.rag.retrieval import reciprocal_rank_fusion
from app.rag.rerank import DashScopeReranker
from app.tools.db_tools import _validate_readonly_query
from app.utils.path_utils import resolve_path
from scripts.migrate_mysql_to_postgres import _postgres_type


def _hit(identifier: str, score: float) -> dict:
    return {"id": identifier, "content": identifier, "metadata": {}, "score": score}


def test_lexicalize_adds_chinese_bigrams() -> None:
    assert lexicalize("企业知识库 API") == "企 业 知 识 库 企业 业知 知识 识库 api"


def test_rrf_merges_sources_and_keeps_source_scores() -> None:
    result = reciprocal_rank_fusion(
        [_hit("a", 0.8), _hit("b", 0.7)],
        [_hit("b", 0.9), _hit("c", 0.6)],
        top_k=3,
    )
    assert result[0]["id"] == "b"
    assert result[0]["dense_score"] == 0.7
    assert result[0]["sparse_score"] == 0.9


@pytest.mark.parametrize(
    "query",
    [
        "DELETE FROM products",
        "SELECT 1; SELECT 2",
        "SELECT * FROM rag_chunks",
        "SELECT * FROM products FOR UPDATE",
    ],
)
def test_database_agent_rejects_unsafe_sql(query: str) -> None:
    with pytest.raises(ValueError):
        _validate_readonly_query(query)


def test_database_agent_accepts_readonly_cte() -> None:
    assert _validate_readonly_query("WITH p AS (SELECT 1 AS id) SELECT * FROM p")


def test_session_path_is_fail_closed(tmp_path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    assert resolve_path("report.md", str(session)) == str(session / "report.md")
    with pytest.raises(ValueError):
        resolve_path("../escape.txt", str(session))
    with pytest.raises(ValueError):
        resolve_path("/etc/passwd", str(session))


def test_mysql_type_mapping_preserves_unsigned_bigint_range() -> None:
    column = {"DATA_TYPE": "bigint", "COLUMN_TYPE": "bigint unsigned"}
    assert _postgres_type(column) == "numeric(20)"


def test_mysql_json_maps_to_postgres_jsonb() -> None:
    column = {"DATA_TYPE": "json", "COLUMN_TYPE": "json"}
    assert _postgres_type(column) == "jsonb"


@pytest.mark.asyncio
async def test_dashscope_reranker_maps_indices(monkeypatch) -> None:
    import app.rag.rerank as module

    monkeypatch.setattr(
        module,
        "settings",
        SimpleNamespace(
            dashscope_api_key="test-key",
            rerank_model="qwen3-rerank",
            rerank_url="https://example.invalid/rerank",
            rerank_timeout_seconds=1,
            rerank_failure_mode="fail",
        ),
    )

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "output": {
                    "results": [
                        {"index": 1, "relevance_score": 0.9},
                        {"index": 0, "relevance_score": 0.4},
                    ]
                }
            }

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    result, warnings = await DashScopeReranker().rerank(
        query="query",
        candidates=[_hit("a", 0.1), _hit("b", 0.2)],
        top_k=2,
    )
    assert [item["id"] for item in result] == ["b", "a"]
    assert result[0]["rerank_score"] == 0.9
    assert warnings == []


@pytest.mark.asyncio
async def test_compiled_rag_graph_returns_evidence(monkeypatch) -> None:
    import app.rag.graph as module

    document_id = uuid4()
    knowledge_base_id = uuid4()
    chunk_id = uuid4()

    async def fake_embed(_query):
        return [0.0]

    class Dense:
        async def aretrieve(self, **_kwargs):
            return [
                NodeWithScore(
                    node=TextNode(
                        id_=str(chunk_id),
                        text="企业退款期限为七天。",
                        metadata={
                            "document_id": str(document_id),
                            "knowledge_base_id": str(knowledge_base_id),
                            "filename": "制度.md",
                            "page_start": 2,
                            "page_end": 2,
                            "section_path": ["退款"],
                        },
                    ),
                    score=0.88,
                )
            ]

    class Sparse:
        async def aretrieve(self, **_kwargs):
            return []

    class Reranker:
        async def rerank(self, *, candidates, top_k, **_kwargs):
            output = [dict(item) for item in candidates[:top_k]]
            output[0]["rerank_score"] = 0.95
            return output, []

    monkeypatch.setattr(module, "embed_query", fake_embed)
    monkeypatch.setattr(module, "dense_retriever", Dense())
    monkeypatch.setattr(module, "sparse_retriever", Sparse())
    monkeypatch.setattr(module, "reranker", Reranker())
    result = await module.rag_graph.ainvoke(
        {
            "messages": [HumanMessage(content="退款期限？")],
            "tenant_id": "00000000-0000-0000-0000-000000000001",
            "top_k": 3,
        }
    )
    response = result["response"]
    assert response["evidence"][0]["chunk_id"] == str(chunk_id)
    assert response["evidence"][0]["scores"]["rerank"] == 0.95
    assert "[S1]" in result["messages"][-1].content
    UUID(response["query_id"])
