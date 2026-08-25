"""Async PostgreSQL repository for chat conversation history."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from uuid import uuid4

from psycopg.errors import UniqueViolation

from app.core.postgres import create_postgres_pool
from app.rag.config import settings


class ActiveRunExistsError(RuntimeError):
    """Raised when a conversation already owns a non-terminal run."""


class ChatDatabase:
    def __init__(self) -> None:
        self.pool = create_postgres_pool(
            settings.database_url,
            name="chat-db",
            min_size=1,
            max_size=4,
        )

    async def open(self) -> None:
        await self.pool.open()
        await self.pool.wait(timeout=self.pool.timeout)

    async def close(self) -> None:
        await self.pool.close()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[Any]:
        async with self.pool.connection() as connection:
            yield connection

    async def migrate(self) -> None:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS chat_conversations (
                id uuid PRIMARY KEY,
                title text NOT NULL DEFAULT '新会话',
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """,
            "CREATE INDEX IF NOT EXISTS chat_conversations_updated_idx ON chat_conversations (updated_at DESC)",
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                id uuid PRIMARY KEY,
                conversation_id uuid NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
                run_id uuid,
                role text NOT NULL CHECK (role IN ('user', 'assistant')),
                content text NOT NULL DEFAULT '',
                events jsonb NOT NULL DEFAULT '[]'::jsonb,
                files jsonb NOT NULL DEFAULT '[]'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """,
            "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS run_id uuid",
            "CREATE INDEX IF NOT EXISTS chat_messages_conv_idx ON chat_messages (conversation_id, created_at)",
            "CREATE UNIQUE INDEX IF NOT EXISTS chat_messages_one_assistant_per_run ON chat_messages (run_id) WHERE run_id IS NOT NULL AND role = 'assistant'",
            """
            CREATE TABLE IF NOT EXISTS agent_runs (
                id uuid PRIMARY KEY,
                conversation_id uuid NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
                tenant_id uuid NOT NULL,
                query text NOT NULL,
                status text NOT NULL,
                attempt integer NOT NULL DEFAULT 0,
                worker_id text,
                error_message text,
                created_at timestamptz NOT NULL DEFAULT now(),
                started_at timestamptz,
                completed_at timestamptz,
                last_heartbeat_at timestamptz,
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """,
            "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS attempt integer NOT NULL DEFAULT 0",
            "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS last_heartbeat_at timestamptz",
            "CREATE INDEX IF NOT EXISTS agent_runs_conversation_idx ON agent_runs (conversation_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS agent_runs_active_idx ON agent_runs (conversation_id, status) WHERE status IN ('queued', 'running', 'cancelling')",
            "CREATE UNIQUE INDEX IF NOT EXISTS agent_runs_one_active_per_conversation ON agent_runs (conversation_id) WHERE status IN ('queued', 'running', 'cancelling')",
        ]
        async with self.connection() as connection:
            async with connection.transaction():
                for statement in statements:
                    await connection.execute(statement)

    async def upsert_conversation(self, thread_id: str) -> None:
        async with self.connection() as connection:
            await connection.execute(
                """
                INSERT INTO chat_conversations (id)
                VALUES (%s)
                ON CONFLICT (id) DO UPDATE SET updated_at = now()
                """,
                (thread_id,),
            )

    async def conversation_exists(self, thread_id: str) -> bool:
        async with self.connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT 1 AS ok FROM chat_conversations WHERE id = %s",
                    (thread_id,),
                )
            ).fetchone()
            return bool(row)

    async def health(self) -> bool:
        try:
            async with self.connection() as connection:
                row = await (await connection.execute("SELECT 1 AS ok")).fetchone()
                return bool(row and row["ok"] == 1)
        except Exception:
            return False

    async def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        events: list[dict[str, Any]] | None = None,
        files: list[dict[str, Any]] | None = None,
    ) -> None:
        message_id = uuid4()
        events_json = json.dumps(events or [], ensure_ascii=False)
        files_json = json.dumps(files or [], ensure_ascii=False)
        async with self.connection() as connection:
            async with connection.transaction():
                if role == "user":
                    # 标题只在首条用户消息时生成一次，后续消息不覆盖用户可能看过的标题
                    await connection.execute(
                        """
                        UPDATE chat_conversations
                        SET title = left(%s, 40), updated_at = now()
                        WHERE id = %s
                          AND NOT EXISTS (
                              SELECT 1 FROM chat_messages
                              WHERE conversation_id = %s AND role = 'user'
                          )
                        """,
                        (content, conversation_id, conversation_id),
                    )
                await connection.execute(
                    """
                    INSERT INTO chat_messages (id, conversation_id, role, content, events, files)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb)
                    """,
                    (
                        message_id,
                        conversation_id,
                        role,
                        content,
                        events_json,
                        files_json,
                    ),
                )
                await connection.execute(
                    "UPDATE chat_conversations SET updated_at = now() WHERE id = %s",
                    (conversation_id,),
                )

    async def create_run(
        self,
        run_id: str,
        conversation_id: str,
        tenant_id: str,
        query: str,
    ) -> dict[str, Any]:
        """Persist the durable business record before dispatching work to Redis."""
        try:
            async with self.connection() as connection:
                row = await (
                    await connection.execute(
                        """
                        INSERT INTO agent_runs (id, conversation_id, tenant_id, query, status)
                        VALUES (%s, %s, %s, %s, 'queued')
                        RETURNING *
                        """,
                        (run_id, conversation_id, tenant_id, query),
                    )
                ).fetchone()
                return dict(row)
        except UniqueViolation as error:
            if error.diag.constraint_name == "agent_runs_one_active_per_conversation":
                raise ActiveRunExistsError(conversation_id) from error
            raise

    async def create_run_with_user_message(
        self,
        run_id: str,
        conversation_id: str,
        tenant_id: str,
        query: str,
    ) -> dict[str, Any]:
        """Atomically create the conversation, user message, and queued run."""
        message_id = uuid4()
        try:
            async with self.connection() as connection:
                async with connection.transaction():
                    await connection.execute(
                        """
                        INSERT INTO chat_conversations (id)
                        VALUES (%s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (conversation_id,),
                    )
                    row = await (
                        await connection.execute(
                            """
                            INSERT INTO agent_runs (
                                id, conversation_id, tenant_id, query, status
                            )
                            VALUES (%s, %s, %s, %s, 'queued')
                            RETURNING *
                            """,
                            (run_id, conversation_id, tenant_id, query),
                        )
                    ).fetchone()
                    await connection.execute(
                        """
                        UPDATE chat_conversations
                        SET title = left(%s, 40), updated_at = now()
                        WHERE id = %s
                          AND NOT EXISTS (
                              SELECT 1 FROM chat_messages
                              WHERE conversation_id = %s AND role = 'user'
                          )
                        """,
                        (query, conversation_id, conversation_id),
                    )
                    await connection.execute(
                        """
                        INSERT INTO chat_messages (
                            id, conversation_id, role, content, events, files
                        )
                        VALUES (%s, %s, 'user', %s, '[]'::jsonb, '[]'::jsonb)
                        """,
                        (message_id, conversation_id, query),
                    )
                    return dict(row)
        except UniqueViolation as error:
            if error.diag.constraint_name == "agent_runs_one_active_per_conversation":
                raise ActiveRunExistsError(conversation_id) from error
            raise

    async def update_run_status(
        self,
        run_id: str,
        status: str,
        *,
        worker_id: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        """Update lifecycle timestamps without moving business truth into Redis."""
        async with self.connection() as connection:
            row = await (
                await connection.execute(
                """
                UPDATE agent_runs
                SET status = %s,
                    worker_id = COALESCE(%s, worker_id),
                    error_message = COALESCE(%s, error_message),
                    started_at = CASE
                        WHEN %s = 'running' THEN COALESCE(started_at, now())
                        ELSE started_at
                    END,
                    last_heartbeat_at = CASE
                        WHEN %s = 'running' THEN now()
                        ELSE last_heartbeat_at
                    END,
                    completed_at = CASE
                        WHEN %s IN ('completed', 'failed', 'cancelled') THEN now()
                        ELSE completed_at
                    END,
                    updated_at = now()
                WHERE id = %s
                  AND (
                    (%s = 'running' AND status = 'queued')
                    OR (%s = 'cancelling' AND status IN ('queued', 'running', 'cancelling'))
                    OR (%s IN ('completed', 'failed', 'cancelled')
                        AND status IN ('queued', 'running', 'cancelling'))
                  )
                RETURNING id
                """,
                (
                    status,
                    worker_id,
                    error_message,
                    status,
                    status,
                    status,
                    run_id,
                    status,
                    status,
                    status,
                ),
            )
            ).fetchone()
            return row is not None

    async def heartbeat_run(self, run_id: str, worker_id: str) -> bool:
        """Renew the durable lease for one actively running delivery."""
        async with self.connection() as connection:
            row = await (
                await connection.execute(
                    """
                    UPDATE agent_runs
                    SET last_heartbeat_at = now(), updated_at = now()
                    WHERE id = %s AND status = 'running' AND worker_id = %s
                    RETURNING id
                    """,
                    (run_id, worker_id),
                )
            ).fetchone()
            return row is not None

    async def adopt_stale_run(
        self,
        run_id: str,
        worker_id: str,
        *,
        stale_after_seconds: float,
    ) -> bool:
        """Transfer a running lease only after both Redis and PostgreSQL leases expired."""
        async with self.connection() as connection:
            row = await (
                await connection.execute(
                    """
                    UPDATE agent_runs
                    SET worker_id = %s, last_heartbeat_at = now(), updated_at = now()
                    WHERE id = %s
                      AND status = 'running'
                      AND COALESCE(last_heartbeat_at, updated_at)
                          < now() - (%s * interval '1 second')
                    RETURNING id
                    """,
                    (worker_id, run_id, stale_after_seconds),
                )
            ).fetchone()
            return row is not None

    async def reschedule_run(
        self,
        run_id: str,
        *,
        worker_id: str,
        error_message: str,
        max_attempts: int,
    ) -> int | None:
        """Move a transiently failed run back to queued while preserving its identity."""
        async with self.connection() as connection:
            row = await (
                await connection.execute(
                    """
                    UPDATE agent_runs
                    SET status = 'queued', attempt = attempt + 1,
                        worker_id = NULL, error_message = %s,
                        last_heartbeat_at = NULL, updated_at = now()
                    WHERE id = %s AND status = 'running' AND worker_id = %s
                      AND attempt + 1 < %s
                    RETURNING attempt
                    """,
                    (error_message[:4000], run_id, worker_id, max_attempts),
                )
            ).fetchone()
            return int(row["attempt"]) if row else None

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        async with self.connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT * FROM agent_runs WHERE id = %s",
                    (run_id,),
                )
            ).fetchone()
            return dict(row) if row else None

    async def finalize_run(
        self,
        *,
        run_id: str,
        conversation_id: str,
        status: str,
        worker_id: str,
        content: str,
        events: list[dict[str, Any]],
        files: list[dict[str, Any]],
        error_message: str | None = None,
    ) -> bool:
        """Atomically commit the terminal run state and its single assistant message."""
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError(f"Unsupported terminal status: {status}")
        message_id = uuid4()
        async with self.connection() as connection:
            async with connection.transaction():
                row = await (
                    await connection.execute(
                        """
                        UPDATE agent_runs
                        SET status = %s, worker_id = %s, error_message = %s,
                            completed_at = now(), last_heartbeat_at = now(),
                            updated_at = now()
                        WHERE id = %s AND conversation_id = %s
                          AND status IN ('queued', 'running', 'cancelling')
                        RETURNING id
                        """,
                        (
                            status,
                            worker_id,
                            error_message[:4000] if error_message else None,
                            run_id,
                            conversation_id,
                        ),
                    )
                ).fetchone()
                if not row:
                    return False
                await connection.execute(
                    """
                    INSERT INTO chat_messages (
                        id, conversation_id, run_id, role, content, events, files
                    )
                    VALUES (%s, %s, %s, 'assistant', %s, %s::jsonb, %s::jsonb)
                    """,
                    (
                        message_id,
                        conversation_id,
                        run_id,
                        content,
                        json.dumps(events, ensure_ascii=False),
                        json.dumps(files, ensure_ascii=False),
                    ),
                )
                await connection.execute(
                    "UPDATE chat_conversations SET updated_at = now() WHERE id = %s",
                    (conversation_id,),
                )
                return True

    async def get_active_run(self, conversation_id: str) -> dict[str, Any] | None:
        async with self.connection() as connection:
            row = await (
                await connection.execute(
                    """
                    SELECT * FROM agent_runs
                    WHERE conversation_id = %s
                      AND status IN ('queued', 'running', 'cancelling')
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (conversation_id,),
                )
            ).fetchone()
            return dict(row) if row else None

    async def finalize_stale_cancellations(
        self,
        conversation_id: str,
        *,
        stale_after_seconds: float = 5.0,
    ) -> list[str]:
        """Release runs left in cancelling after a worker died or lost its lease."""
        async with self.connection() as connection:
            rows = await (
                await connection.execute(
                    """
                    UPDATE agent_runs
                    SET status = 'cancelled', completed_at = now(), updated_at = now()
                    WHERE conversation_id = %s
                      AND status = 'cancelling'
                      AND updated_at < now() - (%s * interval '1 second')
                    RETURNING id::text AS id
                    """,
                    (conversation_id, stale_after_seconds),
                )
            ).fetchall()
            return [str(row["id"]) for row in rows]

    async def list_conversations(self) -> list[dict[str, Any]]:
        async with self.connection() as connection:
            rows = await (
                await connection.execute(
                    """
                    SELECT conv.id, conv.title, conv.created_at, conv.updated_at,
                           count(msg.id)::bigint AS message_count
                    FROM chat_conversations conv
                    LEFT JOIN chat_messages msg ON msg.conversation_id = conv.id
                    GROUP BY conv.id
                    ORDER BY conv.updated_at DESC
                    """
                )
            ).fetchall()
            return [dict(row) for row in rows]

    async def list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        async with self.connection() as connection:
            rows = await (
                await connection.execute(
                    """
                    SELECT id, role, content, events, files, created_at
                    FROM chat_messages
                    WHERE conversation_id = %s
                    ORDER BY created_at, id
                    """,
                    (conversation_id,),
                )
            ).fetchall()
            return [dict(row) for row in rows]

    async def delete_conversation(self, thread_id: str) -> None:
        """
        删除会话及其全部消息，并清理 LangGraph checkpointer 中该线程的记忆，
        保证删除后不会残留旧上下文。只动 checkpoints/checkpoint_blobs/checkpoint_writes
        三张业务表，checkpoint_migrations 是迁移记录表，不可删除。
        """
        async with self.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    "DELETE FROM chat_messages WHERE conversation_id = %s",
                    (thread_id,),
                )
                await connection.execute(
                    "DELETE FROM chat_conversations WHERE id = %s",
                    (thread_id,),
                )
                for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                    await connection.execute(
                        f"DELETE FROM {table} WHERE thread_id = %s",
                        (thread_id,),
                    )


database = ChatDatabase()


async def initialize_chat_database() -> None:
    await database.open()
    await database.migrate()


async def shutdown_chat_database() -> None:
    await database.close()
