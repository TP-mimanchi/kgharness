"""Horizontally scalable Redis Streams worker for LangGraph Agent runs."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
from pathlib import Path

from app.agent.main_agent import (
    initialize_main_agent,
    main_agent_health,
    shutdown_main_agent,
)
from app.chat.db import (
    database as chat_database,
    initialize_chat_database,
    shutdown_chat_database,
)
from app.core.config import settings
from app.core.errors import TransientRunError
from app.core.redis import TERMINAL_RUN_STATUSES, broker
from app.rag.db import initialize_database, shutdown_database
from app.services.agent_execution import (
    _cancel_before_start,
    execute_run,
    finalize_exhausted_run,
)


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("agent-worker")
HEALTH_FILE = Path(
    os.getenv("AGENT_WORKER_HEALTH_FILE", "/tmp/kgharness-agent-worker.ready")
)


async def _execute_claim(
    consumer_name: str,
    message_id: str,
    payload: dict[str, str],
) -> None:
    run_id = payload["run_id"]
    thread_id = payload["thread_id"]
    query = payload["query"]
    tenant_id = payload["tenant_id"]
    delivery_kind = payload.get("_delivery_kind", "new")
    resume_existing = False
    state = await broker.get_status(run_id)
    database_run = await chat_database.get_run(run_id)
    database_status = str(database_run["status"]) if database_run else "missing"
    terminal_status = (
        database_status
        if database_status in TERMINAL_RUN_STATUSES
        else state.get("status")
    )
    if terminal_status in TERMINAL_RUN_STATUSES:
        await broker.set_status(run_id, str(terminal_status), worker_id=consumer_name)
        await broker.clear_active_run(thread_id, run_id)
        await broker.acknowledge(message_id)
        return

    if delivery_kind == "reclaimed" and database_status == "running":
        resume_existing = await chat_database.adopt_stale_run(
            run_id,
            consumer_name,
            stale_after_seconds=settings.worker_claim_idle_ms / 1000,
        )
        if not resume_existing:
            # PostgreSQL still has a fresh lease. Do not execute concurrently; leave the
            # delivery pending so the active worker can renew or a later reclaim can adopt.
            logger.warning("Refusing premature adoption for run %s", run_id)
            return

    execution = asyncio.create_task(
        execute_run(
            query=query,
            thread_id=thread_id,
            run_id=run_id,
            worker_id=consumer_name,
            tenant_id=tenant_id,
            resume_existing=resume_existing,
        )
    )
    delivery_completed = False
    try:
        loop = asyncio.get_running_loop()
        next_heartbeat_at = loop.time() + settings.worker_heartbeat_seconds
        while True:
            done, _ = await asyncio.wait(
                {execution},
                timeout=settings.worker_cancel_poll_seconds,
            )
            if execution in done:
                break
            now = loop.time()
            if now >= next_heartbeat_at:
                await broker.touch_claim(message_id, consumer_name)
                await chat_database.heartbeat_run(run_id, consumer_name)
                next_heartbeat_at = now + settings.worker_heartbeat_seconds
            if await broker.cancel_requested(run_id):
                execution.cancel()
        await execution
        delivery_completed = True
    except TransientRunError as error:
        current = await chat_database.get_run(run_id)
        current_worker = str(current.get("worker_id") or "") if current else ""
        if current and current["status"] == "running" and current_worker == consumer_name:
            next_attempt = await chat_database.reschedule_run(
                run_id,
                worker_id=consumer_name,
                error_message=str(error),
                max_attempts=settings.run_max_attempts,
            )
            if next_attempt is not None:
                delay = settings.run_retry_base_seconds * (
                    2 ** max(0, next_attempt - 1)
                )
                await asyncio.sleep(delay)
                await broker.requeue_run(
                    run_id=run_id,
                    thread_id=thread_id,
                    tenant_id=tenant_id,
                    query=query,
                    attempt=next_attempt,
                    error=str(error),
                )
            else:
                latest = await chat_database.get_run(run_id)
                latest_status = str(latest["status"]) if latest else "missing"
                if latest_status in {"cancelling", "cancelled"}:
                    await _cancel_before_start(
                        run_id=run_id,
                        thread_id=thread_id,
                        worker_id=consumer_name,
                    )
                else:
                    await finalize_exhausted_run(
                        run_id=run_id,
                        thread_id=thread_id,
                        worker_id=consumer_name,
                        error=str(error),
                    )
            delivery_completed = True
        else:
            logger.exception("Transient run failure could not be rescheduled: %s", run_id)
    except asyncio.CancelledError:
        execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)
        raise
    except Exception:
        logger.exception("Run %s failed outside the execution service", run_id)
    finally:
        if delivery_completed:
            await broker.acknowledge(message_id)


async def _worker_heartbeat_loop(worker_id: str, stop_event: asyncio.Event) -> None:
    """Publish process liveness independently of individual run leases."""
    while not stop_event.is_set():
        try:
            if await main_agent_health():
                await broker.heartbeat_worker(worker_id)
                HEALTH_FILE.touch(exist_ok=True)
            else:
                logger.error("Checkpoint pool health check failed; worker is not ready")
        except Exception:
            logger.exception("Worker heartbeat failed")
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=settings.worker_heartbeat_seconds
            )
        except TimeoutError:
            continue


async def _consumer_loop(consumer_name: str, stop_event: asyncio.Event) -> None:
    logger.info("Agent consumer started: %s", consumer_name)
    while not stop_event.is_set():
        try:
            claimed = await broker.claim_run(consumer_name)
            if claimed is None:
                continue
            message_id, payload = claimed
            await _execute_claim(consumer_name, message_id, payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Agent consumer loop failed; retrying")
            await asyncio.sleep(2)


async def run_worker() -> None:
    if not settings.distributed:
        raise RuntimeError("Agent worker requires RUN_EXECUTION_MODE=distributed")

    await broker.connect(required=True)
    await initialize_database()
    await initialize_chat_database()
    await initialize_main_agent()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, stop_event.set)

    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    await broker.heartbeat_worker(worker_id)
    HEALTH_FILE.touch(exist_ok=True)
    heartbeat_task = asyncio.create_task(_worker_heartbeat_loop(worker_id, stop_event))
    consumers = [
        asyncio.create_task(_consumer_loop(f"{worker_id}:{index}", stop_event))
        for index in range(settings.worker_concurrency)
    ]
    logger.info("Agent worker started with %s consumers", len(consumers))
    try:
        await stop_event.wait()
    finally:
        # Leave readiness before draining so API nodes stop accepting new work when
        # this is the last Worker in a rolling deployment.
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        await broker.unregister_worker(worker_id)
        HEALTH_FILE.unlink(missing_ok=True)
        done, pending = await asyncio.wait(
            consumers,
            timeout=settings.worker_shutdown_grace_seconds,
        )
        del done
        for consumer in pending:
            consumer.cancel()
        await asyncio.gather(*consumers, return_exceptions=True)
        await shutdown_main_agent()
        await shutdown_database()
        await shutdown_chat_database()
        await broker.close()
        logger.info("Agent worker stopped")


if __name__ == "__main__":
    asyncio.run(run_worker())
