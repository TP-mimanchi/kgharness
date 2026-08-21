"""Horizontally scalable Redis Streams worker for LangGraph Agent runs."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket

from app.agent.main_agent import initialize_main_agent, shutdown_main_agent
from app.chat.db import initialize_chat_database, shutdown_chat_database
from app.core.config import settings
from app.core.redis import TERMINAL_RUN_STATUSES, broker
from app.rag.db import initialize_database, shutdown_database
from app.services.agent_execution import execute_run


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("agent-worker")


async def _execute_claim(
    consumer_name: str,
    message_id: str,
    payload: dict[str, str],
) -> None:
    run_id = payload["run_id"]
    thread_id = payload["thread_id"]
    query = payload["query"]
    tenant_id = payload["tenant_id"]
    state = await broker.get_status(run_id)
    if state.get("status") in TERMINAL_RUN_STATUSES:
        await broker.acknowledge(message_id)
        return

    execution = asyncio.create_task(
        execute_run(
            query=query,
            thread_id=thread_id,
            run_id=run_id,
            worker_id=consumer_name,
            tenant_id=tenant_id,
        )
    )
    delivery_completed = False
    try:
        while True:
            done, _ = await asyncio.wait(
                {execution},
                timeout=settings.worker_heartbeat_seconds,
            )
            if execution in done:
                break
            await broker.touch_claim(message_id, consumer_name)
            if await broker.cancel_requested(run_id):
                execution.cancel()
        await execution
        delivery_completed = True
    except asyncio.CancelledError:
        execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)
        raise
    except Exception:
        logger.exception("Run %s failed outside the execution service", run_id)
    finally:
        if delivery_completed:
            await broker.acknowledge(message_id)


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
    consumers = [
        asyncio.create_task(_consumer_loop(f"{worker_id}:{index}", stop_event))
        for index in range(settings.worker_concurrency)
    ]
    logger.info("Agent worker started with %s consumers", len(consumers))
    try:
        await stop_event.wait()
    finally:
        for consumer in consumers:
            consumer.cancel()
        await asyncio.gather(*consumers, return_exceptions=True)
        await shutdown_main_agent()
        await shutdown_database()
        await shutdown_chat_database()
        await broker.close()
        logger.info("Agent worker stopped")


if __name__ == "__main__":
    asyncio.run(run_worker())
