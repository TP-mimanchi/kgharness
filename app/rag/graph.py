"""LangGraph RAG subgraph registered as a DeepAgents CompiledSubAgent."""

from __future__ import annotations

import re
import time
from typing import Annotated, Any, Literal, TypedDict
from uuid import UUID

from langchain_core.messages import AIMessage, AnyMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app.rag.config import settings
from app.rag.retrieval import (
    build_retrieval_response,
    dense_retriever,
    embed_query,
    reciprocal_rank_fusion,
    serialize_nodes,
    sparse_retriever,
)
from app.rag.rerank import reranker


def _merge_timings(left: dict[str, float], right: dict[str, float]) -> dict[str, float]:
    return {**left, **right}


class RAGState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    query: str
    tenant_id: str
    knowledge_base_ids: list[str]
    top_k: int
    debug: bool
    query_embedding: list[float]
    dense_hits: list[dict[str, Any]]
    sparse_hits: list[dict[str, Any]]
    fused_hits: list[dict[str, Any]]
    warnings: list[str]
    response: dict[str, Any]
    retry_count: int
    route: Literal["finish", "rewrite"]
    started_at: float
    timings: Annotated[dict[str, float], _merge_timings]


def _parse_request(state: RAGState) -> dict[str, Any]:
    query = state.get("query", "").strip()
    if not query:
        for message in reversed(state.get("messages", [])):
            content = getattr(message, "content", "")
            if isinstance(content, str) and content.strip():
                query = content.strip()
                break
    if not query:
        raise ValueError("RAG subgraph requires a non-empty query")
    return {
        "query": query,
        "tenant_id": state.get("tenant_id", settings.default_tenant_id),
        "knowledge_base_ids": state.get("knowledge_base_ids", []),
        "top_k": state.get("top_k", settings.final_top_k),
        "debug": state.get("debug", False),
        "retry_count": state.get("retry_count", 0),
        "started_at": time.perf_counter(),
        "timings": {},
    }


async def _prepare_query(state: RAGState) -> dict[str, Any]:
    started = time.perf_counter()
    embedding = await embed_query(state["query"])
    timings = dict(state.get("timings", {}))
    timings["embedding_ms"] = (time.perf_counter() - started) * 1000
    return {"query_embedding": embedding, "timings": timings}


async def _dense_retrieve(state: RAGState) -> dict[str, Any]:
    started = time.perf_counter()
    nodes = await dense_retriever.aretrieve(
        tenant_id=UUID(state["tenant_id"]),
        knowledge_base_ids=[UUID(value) for value in state.get("knowledge_base_ids", [])],
        query_embedding=state["query_embedding"],
        top_k=settings.dense_top_k,
    )
    return {
        "dense_hits": serialize_nodes(nodes),
        "timings": {"dense_ms": (time.perf_counter() - started) * 1000},
    }


async def _sparse_retrieve(state: RAGState) -> dict[str, Any]:
    started = time.perf_counter()
    nodes = await sparse_retriever.aretrieve(
        tenant_id=UUID(state["tenant_id"]),
        knowledge_base_ids=[UUID(value) for value in state.get("knowledge_base_ids", [])],
        query=state["query"],
        top_k=settings.sparse_top_k,
    )
    return {
        "sparse_hits": serialize_nodes(nodes),
        "timings": {"sparse_ms": (time.perf_counter() - started) * 1000},
    }


def _fuse(state: RAGState) -> dict[str, Any]:
    started = time.perf_counter()
    fused = reciprocal_rank_fusion(
        state.get("dense_hits", []),
        state.get("sparse_hits", []),
        top_k=settings.rerank_candidate_k,
    )
    return {
        "fused_hits": fused,
        "timings": {"fusion_ms": (time.perf_counter() - started) * 1000},
    }


async def _rerank(state: RAGState) -> dict[str, Any]:
    started = time.perf_counter()
    hits, warnings = await reranker.rerank(
        query=state["query"],
        candidates=state.get("fused_hits", []),
        top_k=state.get("top_k", settings.final_top_k),
    )
    return {
        "fused_hits": hits,
        "warnings": warnings,
        "timings": {"rerank_ms": (time.perf_counter() - started) * 1000},
    }


def _grade(state: RAGState) -> dict[str, Any]:
    if state.get("fused_hits") or state.get("retry_count", 0) >= 1:
        return {"route": "finish"}
    return {"route": "rewrite"}


def _route_after_grade(state: RAGState) -> Literal["finish", "rewrite"]:
    return state["route"]


def _rewrite(state: RAGState) -> dict[str, Any]:
    query = re.sub(r"^(请|麻烦|帮我|请帮我|请查询|请查找)+", "", state["query"]).strip()
    return {
        "query": query or state["query"],
        "retry_count": state.get("retry_count", 0) + 1,
        "dense_hits": [],
        "sparse_hits": [],
        "fused_hits": [],
    }


def _build_evidence(state: RAGState) -> dict[str, Any]:
    timings: dict[str, float] = {}
    for key, value in state.get("timings", {}).items():
        timings[key] = float(value)
    timings.setdefault("embedding_ms", 0.0)
    timings.setdefault("dense_ms", 0.0)
    timings.setdefault("sparse_ms", 0.0)
    timings.setdefault("fusion_ms", 0.0)
    timings.setdefault("rerank_ms", 0.0)
    timings["total_ms"] = (time.perf_counter() - state["started_at"]) * 1000
    response = build_retrieval_response(
        query=state["query"],
        fused_hits=state.get("fused_hits", []),
        timings=timings,
        debug=state.get("debug", False),
        dense_hits=state.get("dense_hits", []),
        sparse_hits=state.get("sparse_hits", []),
    )
    response.warnings.extend(state.get("warnings", []))
    if not response.evidence:
        content = "企业知识库中没有检索到足够证据，请勿基于模型记忆猜测。"
    else:
        sections = ["已从企业知识库检索到以下证据："]
        for evidence in response.evidence:
            locator = f"第 {evidence.page_start} 页" if evidence.page_start else "页码未知"
            sections.append(
                f"\n[{evidence.citation_id}] {evidence.filename}（{locator}）\n{evidence.content}"
            )
        sections.append("\n请基于以上证据回答，并保留 [S#] 引用；证据不足时明确说明。")
        content = "\n".join(sections)
    return {"response": response.model_dump(mode="json"), "messages": [AIMessage(content=content)]}


def build_rag_graph():
    builder = StateGraph(RAGState)
    builder.add_node("parse_request", _parse_request)
    builder.add_node("prepare_query", _prepare_query)
    builder.add_node("dense_retrieve", _dense_retrieve)
    builder.add_node("sparse_retrieve", _sparse_retrieve)
    builder.add_node("fuse", _fuse)
    builder.add_node("rerank", _rerank)
    builder.add_node("grade_evidence", _grade)
    builder.add_node("rewrite_query", _rewrite)
    builder.add_node("build_evidence", _build_evidence)
    builder.add_edge(START, "parse_request")
    builder.add_edge("parse_request", "prepare_query")
    builder.add_edge("prepare_query", "dense_retrieve")
    builder.add_edge("prepare_query", "sparse_retrieve")
    builder.add_edge(["dense_retrieve", "sparse_retrieve"], "fuse")
    builder.add_edge("fuse", "rerank")
    builder.add_edge("rerank", "grade_evidence")
    builder.add_conditional_edges(
        "grade_evidence",
        _route_after_grade,
        {"finish": "build_evidence", "rewrite": "rewrite_query"},
    )
    builder.add_edge("rewrite_query", "prepare_query")
    builder.add_edge("build_evidence", END)
    return builder.compile()


rag_graph = build_rag_graph()
