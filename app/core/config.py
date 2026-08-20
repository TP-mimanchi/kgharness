"""Validated settings for the distributed task and event infrastructure."""

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
class TaskQueueSettings:
    broker_url: str
    result_backend_url: str
    event_redis_url: str
    agent_queue: str
    ingestion_queue: str
    result_expires_seconds: int
    event_ttl_seconds: int
    event_stream_maxlen: int
    task_soft_time_limit_seconds: int
    task_time_limit_seconds: int
    redis_socket_connect_timeout_seconds: float
    redis_socket_timeout_seconds: float
    dependency_health_timeout_seconds: float

    @classmethod
    def from_env(cls) -> "TaskQueueSettings":
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        return cls(
            broker_url=os.getenv("CELERY_BROKER_URL", f"{redis_url}/0"),
            result_backend_url=os.getenv("CELERY_RESULT_BACKEND", f"{redis_url}/1"),
            event_redis_url=os.getenv("EVENT_REDIS_URL", f"{redis_url}/2"),
            agent_queue=os.getenv("CELERY_AGENT_QUEUE", "agent"),
            ingestion_queue=os.getenv("CELERY_INGESTION_QUEUE", "ingestion"),
            result_expires_seconds=_int_env(
                "CELERY_RESULT_EXPIRES_SECONDS", 86400, 60, 604800
            ),
            event_ttl_seconds=_int_env(
                "EVENT_STREAM_TTL_SECONDS", 86400, 60, 604800
            ),
            event_stream_maxlen=_int_env(
                "EVENT_STREAM_MAXLEN", 1000, 10, 10000
            ),
            task_soft_time_limit_seconds=_int_env(
                "CELERY_TASK_SOFT_TIME_LIMIT_SECONDS", 3300, 30, 86400
            ),
            task_time_limit_seconds=_int_env(
                "CELERY_TASK_TIME_LIMIT_SECONDS", 3600, 60, 90000
            ),
            redis_socket_connect_timeout_seconds=_float_env(
                "REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS", 5.0, 0.5, 60.0
            ),
            redis_socket_timeout_seconds=_float_env(
                "REDIS_SOCKET_TIMEOUT_SECONDS", 5.0, 0.5, 60.0
            ),
            dependency_health_timeout_seconds=_float_env(
                "DEPENDENCY_HEALTH_TIMEOUT_SECONDS", 5.0, 0.5, 30.0
            ),
        )


task_queue_settings = TaskQueueSettings.from_env()
