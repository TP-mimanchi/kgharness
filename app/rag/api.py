"""Versioned knowledge-base, ingestion, and retrieval API."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID, uuid4

import aiofiles
from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile, status
from langchain_core.messages import HumanMessage
from psycopg.errors import UniqueViolation

from app.rag.config import settings
from app.rag.db import database
from app.rag.graph import rag_graph
from app.rag.parsers import SUPPORTED_SUFFIXES
from app.rag.schemas import (
    DocumentView,
    IngestionJobView,
    KnowledgeBaseCreate,
    KnowledgeBaseView,
    RetrievalRequest,
    RetrievalResponse,
)

router = APIRouter(prefix="/api/v1", tags=["enterprise-rag"])


async def _tenant_id(x_tenant_id: str | None = Header(default=None)) -> UUID:
    raw = x_tenant_id or settings.default_tenant_id
    try:
        tenant_id = UUID(raw)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="invalid X-Tenant-ID") from error
    if not await database.tenant_exists(tenant_id):
        raise HTTPException(status_code=403, detail="tenant is not active")
    return tenant_id


@router.get("/rag/health")
async def rag_health() -> dict[str, str]:
    return {"status": "ok" if await database.health() else "error"}


@router.post(
    "/knowledge-bases",
    response_model=KnowledgeBaseView,
    status_code=status.HTTP_201_CREATED,
)
async def create_knowledge_base(
    request: KnowledgeBaseCreate,
    tenant_id: UUID = Depends(_tenant_id),
):
    try:
        row = await database.create_knowledge_base(
            tenant_id, request.name, request.description
        )
    except UniqueViolation as error:
        raise HTTPException(status_code=409, detail="knowledge base name already exists") from error
    return KnowledgeBaseView.model_validate(row)


@router.get("/knowledge-bases", response_model=list[KnowledgeBaseView])
async def list_knowledge_bases(
    tenant_id: UUID = Depends(_tenant_id),
):
    rows = await database.list_knowledge_bases(tenant_id)
    return [KnowledgeBaseView.model_validate(row) for row in rows]


@router.post(
    "/knowledge-bases/{knowledge_base_id}/documents",
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_knowledge_document(
    knowledge_base_id: UUID,
    file: UploadFile = File(...),
    tenant_id: UUID = Depends(_tenant_id),
):
    if not await database.knowledge_base_exists(tenant_id, knowledge_base_id):
        raise HTTPException(status_code=404, detail="knowledge base not found")

    original_filename = Path(file.filename or "document").name
    suffix = Path(original_filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=f"unsupported document format: {suffix or '<none>'}",
        )

    storage_name = f"{uuid4().hex}{suffix}"
    target_dir = settings.storage_dir / str(tenant_id) / str(knowledge_base_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / storage_name
    digest = hashlib.sha256()
    size = 0
    try:
        async with aiofiles.open(target_path, "wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="document exceeds upload limit")
                digest.update(chunk)
                await output.write(chunk)
    except Exception:
        target_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    try:
        document, job, created = await database.create_document_and_job(
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            original_filename=original_filename,
            content_type=file.content_type or "application/octet-stream",
            storage_path=str(target_path),
            size_bytes=size,
            sha256=digest.hexdigest(),
        )
    except Exception:
        target_path.unlink(missing_ok=True)
        raise
    if not created:
        target_path.unlink(missing_ok=True)
    return {
        "created": created,
        "document": DocumentView.model_validate(document),
        "job": IngestionJobView.model_validate(job),
    }


@router.get("/documents/{document_id}", response_model=DocumentView)
async def get_document(
    document_id: UUID,
    tenant_id: UUID = Depends(_tenant_id),
):
    row = await database.get_document(tenant_id, document_id)
    if not row:
        raise HTTPException(status_code=404, detail="document not found")
    return DocumentView.model_validate(row)


@router.get("/ingestion-jobs/{job_id}", response_model=IngestionJobView)
async def get_ingestion_job(
    job_id: UUID,
    tenant_id: UUID = Depends(_tenant_id),
):
    row = await database.get_job(tenant_id, job_id)
    if not row:
        raise HTTPException(status_code=404, detail="ingestion job not found")
    return IngestionJobView.model_validate(row)


@router.post("/ingestion-jobs/{job_id}/retry", response_model=IngestionJobView)
async def retry_ingestion_job(
    job_id: UUID,
    tenant_id: UUID = Depends(_tenant_id),
):
    row = await database.retry_failed_job(tenant_id, job_id)
    if not row:
        existing = await database.get_job(tenant_id, job_id)
        if not existing:
            raise HTTPException(status_code=404, detail="ingestion job not found")
        raise HTTPException(status_code=409, detail="only failed jobs can be retried")
    return IngestionJobView.model_validate(row)


@router.post("/retrieval/search", response_model=RetrievalResponse)
async def search_knowledge(
    request: RetrievalRequest,
    tenant_id: UUID = Depends(_tenant_id),
):
    result = await rag_graph.ainvoke(
        {
            "messages": [HumanMessage(content=request.query)],
            "query": request.query,
            "tenant_id": str(tenant_id),
            "knowledge_base_ids": [str(value) for value in request.knowledge_base_ids],
            "top_k": request.top_k,
            "debug": request.debug,
        }
    )
    return RetrievalResponse.model_validate(result["response"])
