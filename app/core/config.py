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
    run_ttl_seconds: int
    worker_group: str
    worker_concurrency: int
    worker_claim_idle_ms: int
    worker_heartbeat_seconds: float

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

        return cls(
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
        )

    @property
    def distributed(self) -> bool:
        return self.mode == "distributed"

    @property
    def redis_enabled(self) -> bool:
        return bool(self.redis_url)


settings = ExecutionSettings.from_env()
