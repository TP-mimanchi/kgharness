"""One persistent asyncio loop per Celery worker process.

The application repositories and LangGraph checkpointer are async resources. Celery
tasks are synchronous entrypoints, so a process-local event-loop thread keeps pools
bound to one loop across tasks instead of creating and closing a loop per delivery.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any

from app.agent.main_agent import initialize_main_agent, shutdown_main_agent
from app.chat.db import initialize_chat_database, shutdown_chat_database
from app.rag.db import initialize_database, shutdown_database


class WorkerRuntime:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._rag_ready = False
        self._agent_ready = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._started.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="kgharness-worker-asyncio",
            daemon=True,
        )
        self._thread.start()
        if not self._started.wait(timeout=10):
            raise RuntimeError("worker asyncio loop did not start")

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._started.set()
        loop.run_forever()
        loop.close()

    def run(self, coroutine: Coroutine[Any, Any, Any]) -> Any:
        self.start()
        if self._loop is None:
            raise RuntimeError("worker asyncio loop is unavailable")
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop).result()

    async def ensure_rag(self) -> None:
        if not self._rag_ready:
            await initialize_database()
            self._rag_ready = True

    async def ensure_agent(self) -> None:
        if not self._agent_ready:
            # Chat and RAG repositories share one process-wide PostgreSQL pool.
            await self.ensure_rag()
            await initialize_chat_database()
            await initialize_main_agent()
            self._agent_ready = True

    async def _shutdown_resources(self) -> None:
        if self._agent_ready:
            await shutdown_main_agent()
            await shutdown_chat_database()
            self._agent_ready = False
        if self._rag_ready:
            await shutdown_database()
            self._rag_ready = False

    def stop(self) -> None:
        if not self._loop or not self._thread:
            return
        try:
            self.run(self._shutdown_resources())
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=10)
            self._loop = None
            self._thread = None


runtime = WorkerRuntime()
