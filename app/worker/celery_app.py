"""Celery application shared by Agent and ingestion worker deployments."""

from __future__ import annotations

from celery import Celery
from kombu import Exchange, Queue

from app.core.config import task_queue_settings

celery_app = Celery(
    "kgharness",
    broker=task_queue_settings.broker_url,
    backend=task_queue_settings.result_backend_url,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "health_check_interval": 30,
        "retry_on_timeout": True,
        "socket_keepalive": True,
        "socket_connect_timeout": task_queue_settings.redis_socket_connect_timeout_seconds,
        "socket_timeout": task_queue_settings.redis_socket_timeout_seconds,
        "visibility_timeout": task_queue_settings.task_time_limit_seconds + 600,
    },
    redis_socket_connect_timeout=task_queue_settings.redis_socket_connect_timeout_seconds,
    redis_socket_timeout=task_queue_settings.redis_socket_timeout_seconds,
    result_backend_transport_options={
        "retry_on_timeout": True,
        "retry_policy": {
            "max_retries": 2,
            "interval_start": 0.5,
            "interval_step": 0.5,
            "interval_max": 1.0,
        },
    },
    result_expires=task_queue_settings.result_expires_seconds,
    task_track_started=True,
    task_send_sent_event=True,
    worker_send_task_events=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=task_queue_settings.task_soft_time_limit_seconds,
    task_time_limit=task_queue_settings.task_time_limit_seconds,
    task_default_queue=task_queue_settings.agent_queue,
    task_queues=(
        Queue(
            task_queue_settings.agent_queue,
            Exchange(task_queue_settings.agent_queue, type="direct", durable=True),
            routing_key=task_queue_settings.agent_queue,
            durable=True,
        ),
        Queue(
            task_queue_settings.ingestion_queue,
            Exchange(task_queue_settings.ingestion_queue, type="direct", durable=True),
            routing_key=task_queue_settings.ingestion_queue,
            durable=True,
        ),
    ),
    task_routes={
        "kgharness.agent.execute": {"queue": task_queue_settings.agent_queue},
        "kgharness.rag.ingest": {"queue": task_queue_settings.ingestion_queue},
    },
)
