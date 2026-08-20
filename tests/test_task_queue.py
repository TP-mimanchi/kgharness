from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.redis import active_task_key, event_stream_key
from app.worker.celery_app import celery_app


def test_celery_routes_isolate_agent_and_ingestion_workloads() -> None:
    queues = {queue.name for queue in celery_app.conf.task_queues}
    assert queues == {"agent", "ingestion"}
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.task_routes["kgharness.agent.execute"]["queue"] == "agent"
    assert celery_app.conf.task_routes["kgharness.rag.ingest"]["queue"] == "ingestion"


def test_redis_keys_are_namespaced_per_thread() -> None:
    thread_id = str(uuid4())
    assert event_stream_key(thread_id) == f"kgharness:events:{thread_id}"
    assert active_task_key(thread_id) == f"kgharness:active-task:{thread_id}"


@pytest.mark.asyncio
async def test_agent_execution_error_is_not_suppressed(monkeypatch) -> None:
    from app.services import agent_execution

    async def fail(_query: str, _thread_id: str) -> None:
        raise RuntimeError("agent failed")

    monkeypatch.setattr(agent_execution, "run_deep_agent", fail)
    monkeypatch.setattr(agent_execution.monitor, "drain", lambda _thread_id: [])

    with pytest.raises(RuntimeError, match="agent failed"):
        await agent_execution.execute_and_persist_agent("query", str(uuid4()))


def test_chat_repository_reuses_rag_connection_pool() -> None:
    from app.chat.db import database as chat_database
    from app.rag.db import database as rag_database

    assert chat_database._shared_database is rag_database


@pytest.mark.asyncio
async def test_health_check_times_out_instead_of_hanging(monkeypatch) -> None:
    from app.api import server
    from app.rag import db as rag_db

    async def healthy() -> bool:
        return True

    async def hanging() -> bool:
        await asyncio.sleep(1)
        return True

    monkeypatch.setattr(rag_db.database, "health", healthy)
    monkeypatch.setattr(server, "redis_health", hanging)
    monkeypatch.setattr(
        server,
        "task_queue_settings",
        SimpleNamespace(dependency_health_timeout_seconds=0.01),
    )

    with pytest.raises(HTTPException) as error:
        await server.health()

    assert error.value.status_code == 503
    assert error.value.detail == {"database": True, "redis": False}


@pytest.mark.asyncio
async def test_task_status_maps_backend_failure_to_503(monkeypatch) -> None:
    from app.api import server

    class UnavailableResult:
        @property
        def state(self):
            raise TimeoutError("result backend unavailable")

    monkeypatch.setattr(
        server.celery_app,
        "AsyncResult",
        lambda _task_id: UnavailableResult(),
    )

    with pytest.raises(HTTPException) as error:
        await server.get_task_status("00000000-0000-0000-0000-000000000002")

    assert error.value.status_code == 503
    assert error.value.detail == "task result backend unavailable"
