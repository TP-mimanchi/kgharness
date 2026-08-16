"""Persistent PostgreSQL job worker for document ingestion."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket

from app.rag.config import settings
from app.rag.db import initialize_database, shutdown_database, database
from app.rag.ingestion import ingest_job

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("rag-worker")


async def run_worker() -> None:
    await initialize_database()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, stop_event.set)

    logger.info("RAG worker started: %s", worker_id)
    try:
        while not stop_event.is_set():
            job = await database.claim_job(worker_id)
            if not job:
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=settings.worker_poll_seconds
                    )
                except asyncio.TimeoutError:
                    pass
                continue

            logger.info("Processing ingestion job %s", job["id"])
            try:
                await ingest_job(job["id"])
                logger.info("Completed ingestion job %s", job["id"])
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception("Ingestion job %s failed", job["id"])
                await database.fail_job(job["id"], error)
    finally:
        await shutdown_database()
        logger.info("RAG worker stopped")


if __name__ == "__main__":
    asyncio.run(run_worker())
