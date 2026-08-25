"""Shared resilient PostgreSQL pool construction."""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import settings


def create_postgres_pool(
    conninfo: str,
    *,
    name: str,
    min_size: int,
    max_size: int,
    autocommit: bool = False,
    prepare_threshold: int | None = None,
) -> AsyncConnectionPool:
    """Create a pool that validates connections before every checkout."""
    if min_size > max_size:
        raise ValueError(f"{name}: min_size cannot exceed max_size")
    connection_kwargs: dict[str, Any] = {
        "row_factory": dict_row,
        "connect_timeout": settings.postgres_connect_timeout_seconds,
    }
    if autocommit:
        connection_kwargs["autocommit"] = True
    if prepare_threshold is not None:
        connection_kwargs["prepare_threshold"] = prepare_threshold

    return AsyncConnectionPool(
        conninfo=conninfo,
        min_size=min_size,
        max_size=max_size,
        open=False,
        name=name,
        kwargs=connection_kwargs,
        check=AsyncConnectionPool.check_connection,
        timeout=settings.postgres_pool_timeout_seconds,
        max_lifetime=settings.postgres_pool_max_lifetime_seconds,
        max_idle=settings.postgres_pool_max_idle_seconds,
        reconnect_timeout=settings.postgres_pool_reconnect_timeout_seconds,
    )
