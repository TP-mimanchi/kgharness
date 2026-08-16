"""LlamaIndex node adapters and PostgreSQL hybrid retrieval."""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

from llama_index.core.schema import NodeWithScore, TextNode

from app.rag.config import settings
from app.rag.db import database
from app.rag.embeddings import embedding_provider, lexicalize
from app.rag.rerank import reranker
from app.rag.schemas import (
    Evidence,
    RetrievalResponse,
    RetrievalTimings,
    ScoreBreakdown,
)


def _row_to_node(row: dict[str, Any]) -> NodeWithScore:
    node = TextNode(
        id_=str(row["id"]),
        text=row["content"],
        metadata={
            "document_id": str(row["document_id"]),
            "knowledge_base_id": str(row["knowledge_base_id"]),
            "filename": row["original_filename"],
            "page_start": row.get("page_start"),
            "page_end": row.get("page_end"),
            "section_path": row.get("section_path") or [],
        },
    )
    return NodeWithScore(node=node, score=float(row["score"]))


def serialize_nodes(nodes: Sequence[NodeWithScore]) -> list[dict[str, Any]]:
    return [
        {
            "id": node.node_id,
            "content": node.get_content(),
            "metadata": node.metadata,
            "score": float(node.score or 0.0),
        }
        for node in nodes
    ]


class PostgresDenseRetriever:
    async def aretrieve(
        self,
        *,
        tenant_id: UUID,
        knowledge_base_ids: Sequence[UUID],
        query_embedding: Sequence[float],
        top_k: int,
    ) -> list[NodeWithScore]:
        rows = await database.dense_search(
            tenant_id=tenant_id,
            knowledge_base_ids=knowledge_base_ids,
            query_embedding=query_embedding,
            top_k=top_k,
        )
        return [_row_to_node(row) for row in rows]


class PostgresSparseRetriever:
    async def aretrieve(
        self,
        *,
        tenant_id: UUID,
        knowledge_base_ids: Sequence[UUID],
        query: str,
        top_k: int,
    ) -> list[NodeWithScore]:
        lexical_query = lexicalize(query)
        if not lexical_query:
            return []
        rows = await database.sparse_search(
            tenant_id=tenant_id,
            knowledge_base_ids=knowledge_base_ids,
            lexical_query=lexical_query,
            top_k=top_k,
        )
        return [_row_to_node(row) for row in rows]


dense_retriever = PostgresDenseRetriever()
sparse_retriever = PostgresSparseRetriever()


async def embed_query(query: str) -> list[float]:
    vectors = await embedding_provider.embed([query])
    return vectors[0]


def reciprocal_rank_fusion(
    dense_nodes: Sequence[dict[str, Any]],
    sparse_nodes: Sequence[dict[str, Any]],
    *,
    top_k: int,
    rank_constant: int = 60,
) -> list[dict[str, Any]]:
    fused: dict[str, dict[str, Any]] = {}
    for source, nodes in (("dense", dense_nodes), ("sparse", sparse_nodes)):
        for rank, node in enumerate(nodes, start=1):
            entry = fused.setdefault(
                node["id"],
                {
                    **node,
                    "rrf_score": 0.0,
                    "dense_score": None,
                    "sparse_score": None,
                },
            )
            entry["rrf_score"] += 1.0 / (rank_constant + rank)
            entry[f"{source}_score"] = node["score"]
    return sorted(
        fused.values(), key=lambda item: item["rrf_score"], reverse=True
    )[:top_k]


def build_retrieval_response(
    *,
    query: str,
    fused_hits: Sequence[dict[str, Any]],
    timings: dict[str, float],
    debug: bool,
    dense_hits: Sequence[dict[str, Any]],
    sparse_hits: Sequence[dict[str, Any]],
) -> RetrievalResponse:
    evidence: list[Evidence] = []
    for index, hit in enumerate(fused_hits, start=1):
        metadata = hit["metadata"]
        evidence.append(
            Evidence(
                citation_id=f"S{index}",
                chunk_id=UUID(hit["id"]),
                document_id=UUID(metadata["document_id"]),
                knowledge_base_id=UUID(metadata["knowledge_base_id"]),
                filename=metadata["filename"],
                content=hit["content"],
                page_start=metadata.get("page_start"),
                page_end=metadata.get("page_end"),
                section_path=metadata.get("section_path") or [],
                scores=ScoreBreakdown(
                    dense=hit.get("dense_score"),
                    sparse=hit.get("sparse_score"),
                    rrf=hit["rrf_score"],
                    rerank=hit.get("rerank_score"),
                ),
            )
        )
    return RetrievalResponse(
        query_id=uuid4(),
        query=query,
        evidence=evidence,
        insufficient_evidence=not evidence,
        timings=RetrievalTimings(**timings),
        debug=(
            {"dense": list(dense_hits), "sparse": list(sparse_hits), "fused": list(fused_hits)}
            if debug
            else None
        ),
    )


async def hybrid_search(
    *,
    query: str,
    tenant_id: UUID,
    knowledge_base_ids: Sequence[UUID],
    top_k: int | None = None,
    debug: bool = False,
) -> RetrievalResponse:
    started = time.perf_counter()
    embedding_started = time.perf_counter()
    query_embedding = await embed_query(query)
    embedding_ms = (time.perf_counter() - embedding_started) * 1000

    import asyncio

    retrieval_started = time.perf_counter()
    dense_nodes, sparse_nodes = await asyncio.gather(
        dense_retriever.aretrieve(
            tenant_id=tenant_id,
            knowledge_base_ids=knowledge_base_ids,
            query_embedding=query_embedding,
            top_k=settings.dense_top_k,
        ),
        sparse_retriever.aretrieve(
            tenant_id=tenant_id,
            knowledge_base_ids=knowledge_base_ids,
            query=query,
            top_k=settings.sparse_top_k,
        ),
    )
    retrieval_ms = (time.perf_counter() - retrieval_started) * 1000

    fusion_started = time.perf_counter()
    dense_hits = serialize_nodes(dense_nodes)
    sparse_hits = serialize_nodes(sparse_nodes)
    fused_hits = reciprocal_rank_fusion(
        dense_hits,
        sparse_hits,
        top_k=settings.rerank_candidate_k,
    )
    fusion_ms = (time.perf_counter() - fusion_started) * 1000
    rerank_started = time.perf_counter()
    fused_hits, warnings = await reranker.rerank(
        query=query,
        candidates=fused_hits,
        top_k=top_k or settings.final_top_k,
    )
    rerank_ms = (time.perf_counter() - rerank_started) * 1000
    response = build_retrieval_response(
        query=query,
        fused_hits=fused_hits,
        timings={
            "embedding_ms": embedding_ms,
            "dense_ms": retrieval_ms,
            "sparse_ms": retrieval_ms,
            "fusion_ms": fusion_ms,
            "rerank_ms": rerank_ms,
            "total_ms": (time.perf_counter() - started) * 1000,
        },
        debug=debug,
        dense_hits=dense_hits,
        sparse_hits=sparse_hits,
    )
    response.warnings.extend(warnings)
    return response
