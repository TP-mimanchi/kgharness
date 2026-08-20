"""Thin Celery delivery adapters; business logic stays independently testable."""

from __future__ import annotations

import logging
import os
import socket
from uuid import UUID

from celery import Task
from celery.signals import worker_process_init, worker_process_shutdown

from app.core.redis import active_task_key, sync_redis_client
from app.rag.db import database as rag_database
from app.rag.ingestion import ingest_job
from app.services.agent_execution import execute_and_persist_agent
from app.worker.celery_app import celery_app
from app.worker.runtime import runtime

logger = logging.getLogger(__name__)


@worker_process_init.connect
def start_async_runtime(**_kwargs) -> None:
    runtime.start()


@worker_process_shutdown.connect
def stop_async_runtime(**_kwargs) -> None:
    runtime.stop()


@celery_app.task(
    bind=True,
    base=Task,
    name="kgharness.agent.execute",
    acks_late=True,
    reject_on_worker_lost=True,
)
def execute_agent_task(self: Task, query: str, thread_id: str) -> dict[str, str]:
    async def execute() -> None:
        await runtime.ensure_agent()
        await execute_and_persist_agent(query, thread_id)

    logger.info("agent task started task_id=%s thread_id=%s", self.request.id, thread_id)
    try:
        runtime.run(execute())
    finally:
        try:
            key = active_task_key(thread_id)
            client = sync_redis_client()
            if client.get(key) == self.request.id:
                client.delete(key)
        except Exception:
            logger.exception(
                "failed to clear active task task_id=%s thread_id=%s",
                self.request.id,
                thread_id,
            )
    return {"status": "completed", "thread_id": thread_id}


@celery_app.task(
    bind=True,
    base=Task,
    name="kgharness.rag.ingest",
    max_retries=2,
    acks_late=True,
    reject_on_worker_lost=True,
)
def ingest_document_task(self: Task, job_id: str) -> dict[str, str]:
    parsed_job_id = UUID(job_id)
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{self.request.id}"

    async def execute() -> bool:
        await runtime.ensure_rag()
        claimed = await rag_database.claim_job_by_id(worker_id, parsed_job_id)
        if not claimed:
            current = await rag_database.get_job_by_id(parsed_job_id)
            return bool(current and current["status"] == "completed")
        await ingest_job(parsed_job_id)
        return True

    logger.info("ingestion task started task_id=%s job_id=%s", self.request.id, job_id)
    try:
        completed = runtime.run(execute())
    except Exception as error:
        runtime.run(rag_database.fail_job(parsed_job_id, error))
        if self.request.retries < self.max_retries:
            raise self.retry(exc=error, countdown=min(60, 5 * (2**self.request.retries)))
        raise
    return {"status": "completed" if completed else "skipped", "job_id": job_id}
