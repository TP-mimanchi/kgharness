"""Redis Streams transport for durable runs, cancellation, and SSE events."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.core.config import settings


logger = logging.getLogger(__name__)
TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})


class RunBroker:
    """A small, framework-neutral execution transport built on Redis Streams."""

    def __init__(self) -> None:
        self.client: Redis | None = None
        self._pending_publications: set[asyncio.Task[Any]] = set()
        self._publication_tails: dict[str, asyncio.Task[Any]] = {}

    @property
    def enabled(self) -> bool:
        return self.client is not None

    @property
    def queue_key(self) -> str:
        return f"{settings.redis_key_prefix}:runs"

    def event_key(self, run_id: str) -> str:
        return f"{settings.redis_key_prefix}:run:{run_id}:events"

    def state_key(self, run_id: str) -> str:
        return f"{settings.redis_key_prefix}:run:{run_id}:state"

    def cancel_key(self, run_id: str) -> str:
        return f"{settings.redis_key_prefix}:run:{run_id}:cancel"

    def active_run_key(self, thread_id: str) -> str:
        return f"{settings.redis_key_prefix}:thread:{thread_id}:active-run"

    @property
    def worker_registry_key(self) -> str:
        return f"{settings.redis_key_prefix}:workers"

    @property
    def dead_letter_key(self) -> str:
        return f"{settings.redis_key_prefix}:runs:dead-letter"

    async def connect(self, *, required: bool = False) -> bool:
        if not settings.redis_url:
            if required:
                raise RuntimeError("REDIS_URL is not configured")
            return False
        if self.client is not None:
            return True

        client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.redis_socket_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
            socket_keepalive=True,
            retry_on_timeout=True,
            health_check_interval=30,
        )
        try:
            await client.ping()
            try:
                await client.xgroup_create(
                    self.queue_key,
                    settings.worker_group,
                    id="0-0",
                    mkstream=True,
                )
            except ResponseError as error:
                if "BUSYGROUP" not in str(error):
                    raise
        except Exception:
            await client.aclose()
            if required:
                raise
            logger.exception("Redis is unavailable; continuing without Redis transport")
            return False

        self.client = client
        return True

    async def close(self) -> None:
        await self.flush()
        if self.client is not None:
            await self.client.aclose()
            self.client = None

    async def health(self) -> bool:
        if self.client is None:
            return False
        try:
            return bool(await self.client.ping())
        except Exception:
            return False

    async def heartbeat_worker(self, worker_id: str) -> None:
        """Register a live worker using a timestamped lease."""
        client = self._require_client()
        now = datetime.now(UTC).timestamp()
        cutoff = now - settings.worker_registry_ttl_seconds
        async with client.pipeline(transaction=True) as pipeline:
            pipeline.zadd(self.worker_registry_key, {worker_id: now})
            pipeline.zremrangebyscore(self.worker_registry_key, "-inf", cutoff)
            pipeline.expire(
                self.worker_registry_key,
                max(settings.run_ttl_seconds, settings.worker_registry_ttl_seconds * 2),
            )
            await pipeline.execute()

    async def unregister_worker(self, worker_id: str) -> None:
        await self._require_client().zrem(self.worker_registry_key, worker_id)

    async def live_worker_count(self) -> int:
        client = self._require_client()
        cutoff = datetime.now(UTC).timestamp() - settings.worker_registry_ttl_seconds
        await client.zremrangebyscore(self.worker_registry_key, "-inf", cutoff)
        return int(await client.zcount(self.worker_registry_key, cutoff, "+inf"))

    async def queue_stats(self) -> dict[str, int]:
        """Return consumer-group pressure for readiness checks and alerting."""
        groups = await self._require_client().xinfo_groups(self.queue_key)
        group = next(
            (item for item in groups if item.get("name") == settings.worker_group),
            None,
        )
        if not group:
            return {
                "pending": 0,
                "lag": 0,
                "consumers": 0,
                "dead_letters": int(
                    await self._require_client().xlen(self.dead_letter_key)
                ),
            }
        return {
            "pending": int(group.get("pending") or 0),
            "lag": int(group.get("lag") or 0),
            "consumers": int(group.get("consumers") or 0),
            "dead_letters": int(
                await self._require_client().xlen(self.dead_letter_key)
            ),
        }

    async def enqueue_run(
        self,
        *,
        run_id: str,
        thread_id: str,
        query: str,
        tenant_id: str,
    ) -> str:
        client = self._require_client()
        now = datetime.now(UTC).isoformat()
        async with client.pipeline(transaction=True) as pipeline:
            pipeline.hset(
                self.state_key(run_id),
                mapping={
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "tenant_id": tenant_id,
                    "status": "queued",
                    "attempt": "0",
                    "created_at": now,
                    "updated_at": now,
                },
            )
            pipeline.expire(self.state_key(run_id), settings.run_ttl_seconds)
            pipeline.set(
                self.active_run_key(thread_id),
                run_id,
                ex=settings.run_ttl_seconds,
            )
            pipeline.xadd(
                self.queue_key,
                {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "tenant_id": tenant_id,
                    "query": query,
                    "attempt": "0",
                },
            )
            results = await pipeline.execute()
        await self.publish_event(
            run_id,
            self.build_event(
                "run_queued",
                "任务已进入执行队列",
                run_id=run_id,
                thread_id=thread_id,
                data={"status": "queued"},
            ),
        )
        return str(results[-1])

    async def set_status(
        self,
        run_id: str,
        status: str,
        *,
        worker_id: str | None = None,
        error: str | None = None,
    ) -> None:
        client = self._require_client()
        mapping = {
            "status": status,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        if worker_id:
            mapping["worker_id"] = worker_id
        if error:
            mapping["error"] = error[:4000]
        await client.hset(self.state_key(run_id), mapping=mapping)
        await client.expire(self.state_key(run_id), settings.run_ttl_seconds)

    async def requeue_run(
        self,
        *,
        run_id: str,
        thread_id: str,
        tenant_id: str,
        query: str,
        attempt: int,
        error: str,
    ) -> str:
        """Publish the next delivery for the same durable run identity."""
        client = self._require_client()
        now = datetime.now(UTC).isoformat()
        async with client.pipeline(transaction=True) as pipeline:
            pipeline.hset(
                self.state_key(run_id),
                mapping={
                    "status": "queued",
                    "attempt": str(attempt),
                    "updated_at": now,
                    "error": error[:4000],
                },
            )
            pipeline.expire(self.state_key(run_id), settings.run_ttl_seconds)
            pipeline.set(
                self.active_run_key(thread_id),
                run_id,
                ex=settings.run_ttl_seconds,
            )
            pipeline.xadd(
                self.queue_key,
                {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "tenant_id": tenant_id,
                    "query": query,
                    "attempt": str(attempt),
                },
            )
            results = await pipeline.execute()
        await self.publish_event(
            run_id,
            self.build_event(
                "run_retrying",
                f"基础设施暂时不可用，准备第 {attempt + 1} 次执行",
                run_id=run_id,
                thread_id=thread_id,
                data={"status": "queued", "attempt": attempt, "error": error[:500]},
            ),
        )
        return str(results[-1])

    async def dead_letter_run(
        self,
        *,
        run_id: str,
        thread_id: str,
        error: str,
    ) -> str:
        """Retain exhausted deliveries for operational inspection."""
        return str(
            await self._require_client().xadd(
                self.dead_letter_key,
                {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "error": error[:4000],
                    "failed_at": datetime.now(UTC).isoformat(),
                },
                maxlen=10_000,
                approximate=True,
            )
        )

    async def get_status(self, run_id: str) -> dict[str, str]:
        client = self._require_client()
        return dict(await client.hgetall(self.state_key(run_id)))

    async def get_active_run(self, thread_id: str) -> str | None:
        client = self._require_client()
        value = await client.get(self.active_run_key(thread_id))
        return str(value) if value else None

    async def clear_active_run(self, thread_id: str, run_id: str) -> None:
        client = self._require_client()
        key = self.active_run_key(thread_id)
        current = await client.get(key)
        if current == run_id:
            await client.delete(key)

    async def request_cancel(self, run_id: str) -> None:
        client = self._require_client()
        await client.set(self.cancel_key(run_id), "1", ex=settings.run_ttl_seconds)
        await self.set_status(run_id, "cancelling")

    async def cancel_requested(self, run_id: str) -> bool:
        client = self._require_client()
        return bool(await client.exists(self.cancel_key(run_id)))

    async def claim_run(
        self,
        consumer_name: str,
        *,
        block_ms: int = 5_000,
    ) -> tuple[str, dict[str, str]] | None:
        client = self._require_client()

        # Reclaim only abandoned deliveries. Active workers renew their claim lease.
        reclaimed = await client.xautoclaim(
            self.queue_key,
            settings.worker_group,
            consumer_name,
            min_idle_time=settings.worker_claim_idle_ms,
            start_id="0-0",
            count=1,
        )
        if len(reclaimed) >= 2 and reclaimed[1]:
            message_id, fields = reclaimed[1][0]
            payload = dict(fields)
            payload["_delivery_kind"] = "reclaimed"
            return str(message_id), payload

        messages = await client.xreadgroup(
            settings.worker_group,
            consumer_name,
            {self.queue_key: ">"},
            count=1,
            block=block_ms,
        )
        if not messages:
            return None
        _, entries = messages[0]
        if not entries:
            return None
        message_id, fields = entries[0]
        payload = dict(fields)
        payload["_delivery_kind"] = "new"
        return str(message_id), payload

    async def touch_claim(self, message_id: str, consumer_name: str) -> None:
        client = self._require_client()
        await client.xclaim(
            self.queue_key,
            settings.worker_group,
            consumer_name,
            min_idle_time=0,
            message_ids=[message_id],
            idle=0,
            justid=True,
        )

    async def acknowledge(self, message_id: str) -> None:
        client = self._require_client()
        await client.xack(self.queue_key, settings.worker_group, message_id)

    async def publish_event(self, run_id: str, payload: dict[str, Any]) -> str:
        client = self._require_client()
        event_id = await client.xadd(
            self.event_key(run_id),
            {"payload": json.dumps(payload, ensure_ascii=False, default=str)},
            maxlen=settings.event_stream_max_length,
            approximate=True,
        )
        await client.expire(self.event_key(run_id), settings.event_ttl_seconds)
        return str(event_id)

    def publish_event_nowait(self, run_id: str, payload: dict[str, Any]) -> None:
        if self.client is None:
            return
        try:
            previous = self._publication_tails.get(run_id)
            task = asyncio.create_task(
                self._publish_after(previous, run_id, payload)
            )
        except RuntimeError:
            logger.warning("No running event loop; event was not published to Redis")
            return
        self._publication_tails[run_id] = task
        self._pending_publications.add(task)
        task.add_done_callback(
            lambda completed, current_run_id=run_id: self._publication_done(
                current_run_id, completed
            )
        )

    async def _publish_after(
        self,
        previous: asyncio.Task[Any] | None,
        run_id: str,
        payload: dict[str, Any],
    ) -> str:
        if previous is not None:
            await asyncio.gather(previous, return_exceptions=True)
        return await self.publish_event(run_id, payload)

    def _publication_done(self, run_id: str, task: asyncio.Task[Any]) -> None:
        self._pending_publications.discard(task)
        if self._publication_tails.get(run_id) is task:
            self._publication_tails.pop(run_id, None)
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error("Redis event publication failed: %s", error)

    async def flush(self) -> None:
        if self._pending_publications:
            await asyncio.gather(*tuple(self._pending_publications), return_exceptions=True)

    async def read_events(
        self,
        run_id: str,
        after_id: str,
        *,
        block_ms: int = 15_000,
    ) -> list[tuple[str, dict[str, Any]]]:
        client = self._require_client()
        messages = await client.xread(
            {self.event_key(run_id): after_id},
            count=100,
            block=block_ms,
        )
        events: list[tuple[str, dict[str, Any]]] = []
        for _, entries in messages:
            for event_id, fields in entries:
                raw = fields.get("payload")
                if not raw:
                    continue
                events.append((str(event_id), json.loads(raw)))
        return events

    @staticmethod
    def build_event(
        event: str,
        message: str,
        *,
        run_id: str,
        thread_id: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": "monitor_event",
            "event_id": str(uuid.uuid4()),
            "event": event,
            "message": message,
            "data": data or {},
            "run_id": run_id,
            "thread_id": thread_id,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    def _require_client(self) -> Redis:
        if self.client is None:
            raise RuntimeError("Redis broker is not connected")
        return self.client


broker = RunBroker()
