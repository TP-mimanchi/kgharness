"""Execution-plane configuration shared by the API and agent workers."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv


load_dotenv(find_dotenv())


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    value = int(os.getenv(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    value = float(os.getenv(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class ExecutionSettings:
    """Settings for durable run dispatch and realtime event delivery."""

    mode: str
    redis_url: str | None
    redis_key_prefix: str
    redis_socket_timeout_seconds: float
    event_stream_max_length: int
    event_ttl_seconds: int
    event_publish_interval_ms: int
    event_publish_batch_size: int
    run_ttl_seconds: int
    worker_group: str
    worker_concurrency: int
    worker_claim_idle_ms: int
    worker_heartbeat_seconds: float
    worker_cancel_poll_seconds: float
    worker_registry_ttl_seconds: int
    worker_shutdown_grace_seconds: float
    run_max_attempts: int
    run_retry_base_seconds: float
    postgres_pool_timeout_seconds: float
    postgres_pool_max_lifetime_seconds: float
    postgres_pool_max_idle_seconds: float
    postgres_pool_reconnect_timeout_seconds: float
    postgres_connect_timeout_seconds: int
    checkpoint_pool_min_size: int
    checkpoint_pool_max_size: int

    @classmethod
    def from_env(cls) -> "ExecutionSettings":
        mode = os.getenv("RUN_EXECUTION_MODE", "local").strip().lower()
        if mode not in {"local", "distributed"}:
            raise ValueError("RUN_EXECUTION_MODE must be 'local' or 'distributed'")

        redis_url = os.getenv("REDIS_URL", "").strip() or None
        if mode == "distributed" and not redis_url:
            raise ValueError("REDIS_URL is required when RUN_EXECUTION_MODE=distributed")

        prefix = os.getenv("REDIS_KEY_PREFIX", "kgharness").strip().strip(":")
        if not prefix:
            raise ValueError("REDIS_KEY_PREFIX cannot be empty")

        result = cls(
            mode=mode,
            redis_url=redis_url,
            redis_key_prefix=prefix,
            redis_socket_timeout_seconds=_float_env(
                "REDIS_SOCKET_TIMEOUT_SECONDS", 30, 1, 120
            ),
            event_stream_max_length=_int_env(
                "RUN_EVENT_STREAM_MAX_LENGTH", 2000, 100, 100_000
            ),
            event_ttl_seconds=_int_env(
                "RUN_EVENT_TTL_SECONDS", 86_400, 300, 2_592_000
            ),
            event_publish_interval_ms=_int_env(
                "RUN_EVENT_PUBLISH_INTERVAL_MS", 15, 5, 100
            ),
            event_publish_batch_size=_int_env(
                "RUN_EVENT_PUBLISH_BATCH_SIZE", 64, 1, 1_000
            ),
            run_ttl_seconds=_int_env(
                "RUN_STATE_TTL_SECONDS", 604_800, 3_600, 2_592_000
            ),
            worker_group=(
                os.getenv("AGENT_WORKER_GROUP", "kgharness-agent-workers").strip()
                or "kgharness-agent-workers"
            ),
            worker_concurrency=_int_env("AGENT_WORKER_CONCURRENCY", 2, 1, 32),
            worker_claim_idle_ms=_int_env(
                "AGENT_WORKER_CLAIM_IDLE_MS", 120_000, 30_000, 3_600_000
            ),
            worker_heartbeat_seconds=_float_env(
                "AGENT_WORKER_HEARTBEAT_SECONDS", 10, 1, 300
            ),
            worker_cancel_poll_seconds=_float_env(
                "AGENT_CANCEL_POLL_SECONDS", 0.5, 0.1, 5
            ),
            worker_registry_ttl_seconds=_int_env(
                "AGENT_WORKER_REGISTRY_TTL_SECONDS", 45, 10, 600
            ),
            worker_shutdown_grace_seconds=_float_env(
                "AGENT_WORKER_SHUTDOWN_GRACE_SECONDS", 30, 1, 300
            ),
            run_max_attempts=_int_env("AGENT_RUN_MAX_ATTEMPTS", 3, 1, 10),
            run_retry_base_seconds=_float_env(
                "AGENT_RUN_RETRY_BASE_SECONDS", 1, 0.1, 30
            ),
            postgres_pool_timeout_seconds=_float_env(
                "POSTGRES_POOL_TIMEOUT_SECONDS", 15, 1, 120
            ),
            postgres_pool_max_lifetime_seconds=_float_env(
                "POSTGRES_POOL_MAX_LIFETIME_SECONDS", 1800, 60, 86400
            ),
            postgres_pool_max_idle_seconds=_float_env(
                "POSTGRES_POOL_MAX_IDLE_SECONDS", 300, 10, 3600
            ),
            postgres_pool_reconnect_timeout_seconds=_float_env(
                "POSTGRES_POOL_RECONNECT_TIMEOUT_SECONDS", 30, 1, 600
            ),
            postgres_connect_timeout_seconds=_int_env(
                "POSTGRES_CONNECT_TIMEOUT_SECONDS", 10, 1, 120
            ),
            checkpoint_pool_min_size=_int_env(
                "CHECKPOINT_POOL_MIN_SIZE", 1, 1, 16
            ),
            checkpoint_pool_max_size=_int_env(
                "CHECKPOINT_POOL_MAX_SIZE", 4, 1, 32
            ),
        )
        if result.worker_registry_ttl_seconds <= result.worker_heartbeat_seconds * 2:
            raise ValueError(
                "AGENT_WORKER_REGISTRY_TTL_SECONDS must exceed twice "
                "AGENT_WORKER_HEARTBEAT_SECONDS"
            )
        if result.checkpoint_pool_min_size > result.checkpoint_pool_max_size:
            raise ValueError(
                "CHECKPOINT_POOL_MIN_SIZE cannot exceed CHECKPOINT_POOL_MAX_SIZE"
            )
        return result

    @property
    def distributed(self) -> bool:
        return self.mode == "distributed"

    @property
    def redis_enabled(self) -> bool:
        return bool(self.redis_url)


settings = ExecutionSettings.from_env()
