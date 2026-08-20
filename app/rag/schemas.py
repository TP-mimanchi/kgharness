"""Public and internal schemas shared by the RAG API and LangGraph subgraph."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)


class KnowledgeBaseView(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    description: str
    status: str
    document_count: int = 0
    created_at: datetime


class DocumentView(BaseModel):
    id: UUID
    knowledge_base_id: UUID
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    status: str
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class IngestionJobView(BaseModel):
    id: UUID
    document_id: UUID
    status: str
    stage: str
    attempt: int
    progress: int
    error_code: str | None = None
    error_message: str | None = None
    celery_task_id: str | None = None
    created_at: datetime
    updated_at: datetime


class KnowledgeDocumentView(DocumentView):
    """A knowledge-base document together with its latest ingestion job."""

    latest_job: IngestionJobView | None = None


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    knowledge_base_ids: list[UUID] = Field(default_factory=list, max_length=20)
    top_k: int = Field(default=8, ge=1, le=30)
    tenant_id: UUID | None = None
    debug: bool = False


class ScoreBreakdown(BaseModel):
    dense: float | None = None
    sparse: float | None = None
    rrf: float
    rerank: float | None = None


class Evidence(BaseModel):
    citation_id: str
    chunk_id: UUID
    document_id: UUID
    knowledge_base_id: UUID
    filename: str
    content: str
    page_start: int | None = None
    page_end: int | None = None
    section_path: list[str] = Field(default_factory=list)
    scores: ScoreBreakdown


class RetrievalTimings(BaseModel):
    embedding_ms: float = 0
    dense_ms: float = 0
    sparse_ms: float = 0
    fusion_ms: float = 0
    rerank_ms: float = 0
    total_ms: float = 0


class RetrievalResponse(BaseModel):
    query_id: UUID
    query: str
    evidence: list[Evidence]
    insufficient_evidence: bool
    timings: RetrievalTimings
    warnings: list[str] = Field(default_factory=list)
    debug: dict[str, Any] | None = None


class GraphEvidenceState(BaseModel):
    query: str
    knowledge_base_ids: list[UUID] = Field(default_factory=list)
    retry_count: int = 0
    response: RetrievalResponse | None = None
    route: Literal["finish", "rewrite"] = "finish"
