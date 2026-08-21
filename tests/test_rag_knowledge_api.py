from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api import rag_api as api


def _document_row(tenant_id, knowledge_base_id):
    document_id = uuid4()
    now = datetime.now(timezone.utc)
    return {
        "id": document_id,
        "tenant_id": tenant_id,
        "knowledge_base_id": knowledge_base_id,
        "original_filename": "handbook.md",
        "content_type": "text/markdown",
        "storage_path": "/data/handbook.md",
        "size_bytes": 128,
        "sha256": "a" * 64,
        "status": "completed",
        "error_message": None,
        "created_at": now,
        "updated_at": now,
        "latest_job": {
            "id": uuid4(),
            "tenant_id": tenant_id,
            "document_id": document_id,
            "status": "completed",
            "stage": "completed",
            "attempt": 1,
            "progress": 100,
            "available_at": now,
            "locked_by": None,
            "locked_at": None,
            "error_code": None,
            "error_message": None,
            "created_at": now,
            "updated_at": now,
        },
    }


@pytest.mark.asyncio
async def test_list_knowledge_documents_returns_latest_job(monkeypatch) -> None:
    tenant_id = uuid4()
    knowledge_base_id = uuid4()

    async def exists(*_args):
        return True

    async def list_documents(*_args, **_kwargs):
        return [_document_row(tenant_id, knowledge_base_id)]

    monkeypatch.setattr(api.database, "knowledge_base_exists", exists)
    monkeypatch.setattr(api.database, "list_knowledge_base_documents", list_documents)

    result = await api.list_knowledge_documents(
        knowledge_base_id=knowledge_base_id,
        limit=100,
        offset=0,
        tenant_id=tenant_id,
    )

    assert result[0].original_filename == "handbook.md"
    assert result[0].latest_job is not None
    assert result[0].latest_job.progress == 100


@pytest.mark.asyncio
async def test_list_knowledge_documents_rejects_unknown_base(monkeypatch) -> None:
    async def missing(*_args):
        return False

    monkeypatch.setattr(api.database, "knowledge_base_exists", missing)

    with pytest.raises(HTTPException) as error:
        await api.list_knowledge_documents(
            knowledge_base_id=uuid4(),
            limit=100,
            offset=0,
            tenant_id=uuid4(),
        )

    assert error.value.status_code == 404
