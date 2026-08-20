"""Redis primitives for task ownership and replayable per-thread events."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, AsyncIterator

import redis
import redis.asyncio as async_redis

from app.core.config import task_queue_settings

KEY_PREFIX = "kgharness"


def event_stream_key(thread_id: str) -> str:
    return f"{KEY_PREFIX}:events:{thread_id}"


def active_task_key(thread_id: str) -> str:
    return f"{KEY_PREFIX}:active-task:{thread_id}"


@lru_cache(maxsize=1)
def sync_redis_client() -> redis.Redis:
    return redis.Redis.from_url(
        task_queue_settings.event_redis_url,
        decode_responses=True,
        health_check_interval=30,
        socket_keepalive=True,
        socket_connect_timeout=task_queue_settings.redis_socket_connect_timeout_seconds,
        socket_timeout=task_queue_settings.redis_socket_timeout_seconds,
    )


@lru_cache(maxsize=1)
def async_redis_client() -> async_redis.Redis:
    return async_redis.Redis.from_url(
        task_queue_settings.event_redis_url,
        decode_responses=True,
        health_check_interval=30,
        socket_keepalive=True,
        socket_connect_timeout=task_queue_settings.redis_socket_connect_timeout_seconds,
        socket_timeout=task_queue_settings.redis_socket_timeout_seconds,
    )


def publish_thread_event(thread_id: str, payload: dict[str, Any]) -> None:
    """Append an event to a capped stream so reconnecting clients can replay it."""
    client = sync_redis_client()
    key = event_stream_key(thread_id)
    pipeline = client.pipeline(transaction=False)
    pipeline.xadd(
        key,
        {"payload": json.dumps(payload, ensure_ascii=False)},
        maxlen=task_queue_settings.event_stream_maxlen,
        approximate=True,
    )
    pipeline.expire(key, task_queue_settings.event_ttl_seconds)
    pipeline.execute()


async def iter_thread_events(
    thread_id: str, *, last_event_id: str = "0-0"
) -> AsyncIterator[dict[str, Any]]:
    """Replay and then tail a thread's Redis Stream without consumer-group stealing."""
    client = async_redis_client()
    key = event_stream_key(thread_id)
    cursor = last_event_id
    while True:
        batches = await client.xread({key: cursor}, count=100, block=1000)
        for _stream, entries in batches:
            for event_id, fields in entries:
                cursor = event_id
                raw = fields.get("payload")
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                payload.setdefault("event_id", event_id)
                yield payload


async def set_active_task(thread_id: str, task_id: str) -> None:
    await async_redis_client().set(
        active_task_key(thread_id), task_id, ex=task_queue_settings.result_expires_seconds
    )


async def get_active_task(thread_id: str) -> str | None:
    return await async_redis_client().get(active_task_key(thread_id))


async def clear_active_task(thread_id: str, task_id: str | None = None) -> None:
    client = async_redis_client()
    key = active_task_key(thread_id)
    if task_id is None:
        await client.delete(key)
        return
    async with client.pipeline(transaction=True) as pipeline:
        while True:
            try:
                await pipeline.watch(key)
                if await pipeline.get(key) != task_id:
                    await pipeline.reset()
                    return
                pipeline.multi()
                pipeline.delete(key)
                await pipeline.execute()
                return
            except redis.WatchError:
                continue


async def redis_health() -> bool:
    try:
        return bool(await async_redis_client().ping())
    except redis.RedisError:
        return False
