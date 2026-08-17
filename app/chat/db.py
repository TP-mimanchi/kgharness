"""Async PostgreSQL repository for chat conversation history."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from uuid import uuid4

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.rag.config import settings


class ChatDatabase:
    def __init__(self) -> None:
        self.pool = AsyncConnectionPool(
            conninfo=settings.database_url,
            min_size=1,
            max_size=4,
            open=False,
            kwargs={"row_factory": dict_row},
        )

    async def open(self) -> None:
        await self.pool.open()
        await self.pool.wait()

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
                role text NOT NULL CHECK (role IN ('user', 'assistant')),
                content text NOT NULL DEFAULT '',
                events jsonb NOT NULL DEFAULT '[]'::jsonb,
                files jsonb NOT NULL DEFAULT '[]'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """,
            "CREATE INDEX IF NOT EXISTS chat_messages_conv_idx ON chat_messages (conversation_id, created_at)",
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
