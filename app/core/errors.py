"""Execution error classification used by durable retry policy."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
from psycopg import InterfaceError, OperationalError
from psycopg_pool import PoolTimeout
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError


class TransientRunError(RuntimeError):
    """A run failed because a dependency was temporarily unavailable."""


def _error_chain(error: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def is_transient_infrastructure_error(error: BaseException) -> bool:
    """Return True only for failures safe to retry with the same run/message IDs."""
    transient_types = (
        InterfaceError,
        OperationalError,
        PoolTimeout,
        RedisConnectionError,
        RedisTimeoutError,
        httpx.TimeoutException,
        httpx.NetworkError,
    )
    transient_fragments = (
        "the connection is closed",
        "connection is bad",
        "server closed the connection unexpectedly",
        "connection reset by peer",
        "connection refused",
        "temporary failure in name resolution",
        "pool initialization incomplete",
    )
    for candidate in _error_chain(error):
        if isinstance(candidate, transient_types):
            return True
        message = str(candidate).lower()
        if any(fragment in message for fragment in transient_fragments):
            return True
    return False
