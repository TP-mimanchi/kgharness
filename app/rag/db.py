"""Async PostgreSQL repository for RAG metadata, jobs, vectors, and text search."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Sequence
from uuid import UUID, uuid4

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.rag.config import settings


def _vector_literal(values: Sequence[float]) -> str:
    if len(values) != settings.embedding_dimension:
        raise ValueError(
            f"embedding dimension mismatch: expected {settings.embedding_dimension}, got {len(values)}"
        )
    return "[" + ",".join(f"{float(value):.8f}" for value in values) + "]"


class RAGDatabase:
    def __init__(self) -> None:
        self.pool = AsyncConnectionPool(
            conninfo=settings.database_url,
            min_size=1,
            max_size=8,
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
        dimension = settings.embedding_dimension
        statements = [
            "CREATE EXTENSION IF NOT EXISTS vector",
            "CREATE EXTENSION IF NOT EXISTS pg_trgm",
            """
            CREATE TABLE IF NOT EXISTS rag_tenants (
                id uuid PRIMARY KEY,
                name text NOT NULL,
                status text NOT NULL DEFAULT 'active',
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS rag_knowledge_bases (
                id uuid PRIMARY KEY,
                tenant_id uuid NOT NULL REFERENCES rag_tenants(id),
                name varchar(120) NOT NULL,
                description text NOT NULL DEFAULT '',
                status text NOT NULL DEFAULT 'active',
                active_index_version text NOT NULL DEFAULT 'v1',
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                UNIQUE (tenant_id, name)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS rag_documents (
                id uuid PRIMARY KEY,
                tenant_id uuid NOT NULL REFERENCES rag_tenants(id),
                knowledge_base_id uuid NOT NULL REFERENCES rag_knowledge_bases(id),
                original_filename text NOT NULL,
                content_type text NOT NULL,
                storage_path text NOT NULL,
                size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
                sha256 char(64) NOT NULL,
                status text NOT NULL DEFAULT 'uploaded',
                error_message text,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                UNIQUE (knowledge_base_id, sha256)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS rag_ingestion_jobs (
                id uuid PRIMARY KEY,
                tenant_id uuid NOT NULL REFERENCES rag_tenants(id),
                document_id uuid NOT NULL REFERENCES rag_documents(id) ON DELETE CASCADE,
                status text NOT NULL DEFAULT 'queued',
                stage text NOT NULL DEFAULT 'queued',
                attempt integer NOT NULL DEFAULT 0,
                progress integer NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
                available_at timestamptz NOT NULL DEFAULT now(),
                locked_by text,
                locked_at timestamptz,
                error_code text,
                error_message text,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS rag_chunks (
                id uuid PRIMARY KEY,
                tenant_id uuid NOT NULL REFERENCES rag_tenants(id),
                knowledge_base_id uuid NOT NULL REFERENCES rag_knowledge_bases(id),
                document_id uuid NOT NULL REFERENCES rag_documents(id) ON DELETE CASCADE,
                index_version text NOT NULL DEFAULT 'v1',
                chunk_index integer NOT NULL,
                content text NOT NULL,
                lexical_text text NOT NULL,
                search_tsv tsvector GENERATED ALWAYS AS (
                    to_tsvector('simple'::regconfig, lexical_text)
                ) STORED,
                embedding vector({dimension}) NOT NULL,
                page_start integer,
                page_end integer,
                section_path jsonb NOT NULL DEFAULT '[]'::jsonb,
                metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                UNIQUE (document_id, index_version, chunk_index)
            )
            """,
            "CREATE INDEX IF NOT EXISTS rag_jobs_claim_idx ON rag_ingestion_jobs (status, available_at, created_at)",
            "CREATE INDEX IF NOT EXISTS rag_documents_scope_idx ON rag_documents (tenant_id, knowledge_base_id, status)",
            "CREATE INDEX IF NOT EXISTS rag_chunks_scope_idx ON rag_chunks (tenant_id, knowledge_base_id, index_version)",
            "CREATE INDEX IF NOT EXISTS rag_chunks_search_idx ON rag_chunks USING gin (search_tsv)",
            "CREATE INDEX IF NOT EXISTS rag_chunks_embedding_idx ON rag_chunks USING hnsw (embedding vector_cosine_ops)",
        ]
        async with self.connection() as connection:
            async with connection.transaction():
                for statement in statements:
                    await connection.execute(statement)
                await connection.execute(
                    """
                    INSERT INTO rag_tenants (id, name)
                    VALUES (%s, 'Default Tenant')
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (settings.default_tenant_id,),
                )

    async def health(self) -> bool:
        async with self.connection() as connection:
            row = await (await connection.execute("SELECT 1 AS ok")).fetchone()
            return bool(row and row["ok"] == 1)

    async def tenant_exists(self, tenant_id: UUID) -> bool:
        async with self.connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT 1 AS ok FROM rag_tenants WHERE id = %s AND status = 'active'",
                    (tenant_id,),
                )
            ).fetchone()
            return bool(row)

    async def create_knowledge_base(
        self, tenant_id: UUID, name: str, description: str
    ) -> dict[str, Any]:
        knowledge_base_id = uuid4()
        async with self.connection() as connection:
            row = await (
                await connection.execute(
                    """
                    INSERT INTO rag_knowledge_bases (id, tenant_id, name, description)
                    VALUES (%s, %s, %s, %s)
                    RETURNING *, 0::bigint AS document_count
                    """,
                    (knowledge_base_id, tenant_id, name.strip(), description.strip()),
                )
            ).fetchone()
            return dict(row)

    async def list_knowledge_bases(self, tenant_id: UUID) -> list[dict[str, Any]]:
        async with self.connection() as connection:
            rows = await (
                await connection.execute(
                    """
                    SELECT kb.*,
                           count(doc.id) FILTER (WHERE doc.status <> 'deleted') AS document_count
                    FROM rag_knowledge_bases kb
                    LEFT JOIN rag_documents doc ON doc.knowledge_base_id = kb.id
                    WHERE kb.tenant_id = %s AND kb.status = 'active'
                    GROUP BY kb.id
                    ORDER BY kb.created_at DESC
                    """,
                    (tenant_id,),
                )
            ).fetchall()
            return [dict(row) for row in rows]

    async def knowledge_base_exists(self, tenant_id: UUID, knowledge_base_id: UUID) -> bool:
        async with self.connection() as connection:
            row = await (
                await connection.execute(
                    """
                    SELECT 1 AS ok FROM rag_knowledge_bases
                    WHERE id = %s AND tenant_id = %s AND status = 'active'
                    """,
                    (knowledge_base_id, tenant_id),
                )
            ).fetchone()
            return bool(row)

    async def create_document_and_job(
        self,
        *,
        tenant_id: UUID,
        knowledge_base_id: UUID,
        original_filename: str,
        content_type: str,
        storage_path: str,
        size_bytes: int,
        sha256: str,
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        document_id = uuid4()
        job_id = uuid4()
        async with self.connection() as connection:
            async with connection.transaction():
                existing = await (
                    await connection.execute(
                        """
                        SELECT * FROM rag_documents
                        WHERE knowledge_base_id = %s AND sha256 = %s
                        """,
                        (knowledge_base_id, sha256),
                    )
                ).fetchone()
                if existing:
                    job = await (
                        await connection.execute(
                            """
                            SELECT * FROM rag_ingestion_jobs
                            WHERE document_id = %s ORDER BY created_at DESC LIMIT 1
                            """,
                            (existing["id"],),
                        )
                    ).fetchone()
                    return dict(existing), dict(job), False

                document = await (
                    await connection.execute(
                        """
                        INSERT INTO rag_documents (
                            id, tenant_id, knowledge_base_id, original_filename,
                            content_type, storage_path, size_bytes, sha256
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING *
                        """,
                        (
                            document_id,
                            tenant_id,
                            knowledge_base_id,
                            original_filename,
                            content_type,
                            storage_path,
                            size_bytes,
                            sha256,
                        ),
                    )
                ).fetchone()
                job = await (
                    await connection.execute(
                        """
                        INSERT INTO rag_ingestion_jobs (id, tenant_id, document_id)
                        VALUES (%s, %s, %s)
                        RETURNING *
                        """,
                        (job_id, tenant_id, document_id),
                    )
                ).fetchone()
                return dict(document), dict(job), True

    async def get_document(self, tenant_id: UUID, document_id: UUID) -> dict[str, Any] | None:
        async with self.connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT * FROM rag_documents WHERE id = %s AND tenant_id = %s",
                    (document_id, tenant_id),
                )
            ).fetchone()
            return dict(row) if row else None

    async def list_knowledge_base_documents(
        self,
        tenant_id: UUID,
        knowledge_base_id: UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        async with self.connection() as connection:
            rows = await (
                await connection.execute(
                    """
                    SELECT doc.*,
                           CASE
                               WHEN job.id IS NULL THEN NULL
                               ELSE to_jsonb(job)
                           END AS latest_job
                    FROM rag_documents doc
                    LEFT JOIN LATERAL (
                        SELECT candidate.*
                        FROM rag_ingestion_jobs candidate
                        WHERE candidate.document_id = doc.id
                        ORDER BY candidate.created_at DESC
                        LIMIT 1
                    ) job ON true
                    WHERE doc.tenant_id = %s
                      AND doc.knowledge_base_id = %s
                      AND doc.status <> 'deleted'
                    ORDER BY doc.created_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (tenant_id, knowledge_base_id, limit, offset),
                )
            ).fetchall()
            return [dict(row) for row in rows]

    async def get_job(self, tenant_id: UUID, job_id: UUID) -> dict[str, Any] | None:
        async with self.connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT * FROM rag_ingestion_jobs WHERE id = %s AND tenant_id = %s",
                    (job_id, tenant_id),
                )
            ).fetchone()
            return dict(row) if row else None

    async def retry_failed_job(
        self, tenant_id: UUID, job_id: UUID
    ) -> dict[str, Any] | None:
        async with self.connection() as connection:
            async with connection.transaction():
                row = await (
                    await connection.execute(
                        """
                        UPDATE rag_ingestion_jobs
                        SET status = 'retry', stage = 'queued', progress = 0,
                            attempt = 0, available_at = now(), locked_by = NULL,
                            locked_at = NULL, error_code = NULL, error_message = NULL,
                            updated_at = now()
                        WHERE id = %s AND tenant_id = %s AND status = 'failed'
                        RETURNING *
                        """,
                        (job_id, tenant_id),
                    )
                ).fetchone()
                if not row:
                    return None
                await connection.execute(
                    """
                    UPDATE rag_documents doc
                    SET status = 'queued', error_message = NULL, updated_at = now()
                    FROM rag_ingestion_jobs job
                    WHERE job.id = %s AND doc.id = job.document_id
                    """,
                    (job_id,),
                )
                return dict(row)

    async def claim_job(self, worker_id: str) -> dict[str, Any] | None:
        async with self.connection() as connection:
            async with connection.transaction():
                row = await (
                    await connection.execute(
                        """
                        WITH candidate AS (
                            SELECT id FROM rag_ingestion_jobs
                            WHERE (
                                status IN ('queued', 'retry') AND available_at <= now()
                            ) OR (
                                status = 'processing' AND locked_at < now() - interval '30 minutes'
                            )
                            ORDER BY created_at
                            FOR UPDATE SKIP LOCKED
                            LIMIT 1
                        )
                        UPDATE rag_ingestion_jobs job
                        SET status = 'processing', stage = 'claimed', locked_by = %s,
                            locked_at = now(), attempt = attempt + 1, updated_at = now()
                        FROM candidate
                        WHERE job.id = candidate.id
                        RETURNING job.*
                        """,
                        (worker_id,),
                    )
                ).fetchone()
                return dict(row) if row else None

    async def load_job_document(self, job_id: UUID) -> dict[str, Any] | None:
        async with self.connection() as connection:
            row = await (
                await connection.execute(
                    """
                    SELECT job.id AS job_id, job.attempt, doc.*,
                           doc.id AS document_id, kb.active_index_version
                    FROM rag_ingestion_jobs job
                    JOIN rag_documents doc ON doc.id = job.document_id
                    JOIN rag_knowledge_bases kb ON kb.id = doc.knowledge_base_id
                    WHERE job.id = %s
                    """,
                    (job_id,),
                )
            ).fetchone()
            return dict(row) if row else None

    async def update_job(
        self,
        job_id: UUID,
        *,
        status: str,
        stage: str,
        progress: int,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        async with self.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    UPDATE rag_ingestion_jobs
                    SET status = %s, stage = %s, progress = %s,
                        error_code = %s, error_message = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    (status, stage, progress, error_code, error_message, job_id),
                )
                document_status = {
                    "completed": "completed",
                    "failed": "failed",
                    "processing": "processing",
                }.get(status)
                if document_status:
                    await connection.execute(
                        """
                        UPDATE rag_documents doc
                        SET status = %s, error_message = %s, updated_at = now()
                        FROM rag_ingestion_jobs job
                        WHERE job.id = %s AND doc.id = job.document_id
                        """,
                        (document_status, error_message, job_id),
                    )

    async def fail_job(self, job_id: UUID, error: Exception, max_attempts: int = 3) -> None:
        message = str(error)[:2000]
        code = type(error).__name__.upper()
        async with self.connection() as connection:
            async with connection.transaction():
                row = await (
                    await connection.execute(
                        "SELECT attempt FROM rag_ingestion_jobs WHERE id = %s FOR UPDATE",
                        (job_id,),
                    )
                ).fetchone()
                if not row:
                    return
                retryable = row["attempt"] < max_attempts
                status = "retry" if retryable else "failed"
                stage = "retry_wait" if retryable else "failed"
                await connection.execute(
                    """
                    UPDATE rag_ingestion_jobs
                    SET status = %s, stage = %s, error_code = %s, error_message = %s,
                        available_at = now() + make_interval(secs => LEAST(60, attempt * 5)),
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (status, stage, code, message, job_id),
                )
                await connection.execute(
                    """
                    UPDATE rag_documents doc
                    SET status = %s, error_message = %s, updated_at = now()
                    FROM rag_ingestion_jobs job
                    WHERE job.id = %s AND doc.id = job.document_id
                    """,
                    ("queued" if retryable else "failed", message, job_id),
                )

    async def replace_document_chunks(
        self,
        *,
        tenant_id: UUID,
        knowledge_base_id: UUID,
        document_id: UUID,
        index_version: str,
        chunks: Sequence[dict[str, Any]],
    ) -> None:
        async with self.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    "DELETE FROM rag_chunks WHERE document_id = %s AND index_version = %s",
                    (document_id, index_version),
                )
                for chunk in chunks:
                    await connection.execute(
                        """
                        INSERT INTO rag_chunks (
                            id, tenant_id, knowledge_base_id, document_id, index_version,
                            chunk_index, content, lexical_text, embedding,
                            page_start, page_end, section_path, metadata
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s::vector,
                            %s, %s, %s::jsonb, %s::jsonb
                        )
                        """,
                        (
                            chunk["id"],
                            tenant_id,
                            knowledge_base_id,
                            document_id,
                            index_version,
                            chunk["chunk_index"],
                            chunk["content"],
                            chunk["lexical_text"],
                            _vector_literal(chunk["embedding"]),
                            chunk.get("page_start"),
                            chunk.get("page_end"),
                            json.dumps(chunk.get("section_path", []), ensure_ascii=False),
                            json.dumps(chunk.get("metadata", {}), ensure_ascii=False),
                        ),
                    )

    def _scope_clause(self, knowledge_base_ids: Sequence[UUID]) -> tuple[str, list[Any]]:
        if not knowledge_base_ids:
            return "", []
        return " AND chunk.knowledge_base_id = ANY(%s::uuid[])", [list(knowledge_base_ids)]

    async def dense_search(
        self,
        *,
        tenant_id: UUID,
        knowledge_base_ids: Sequence[UUID],
        query_embedding: Sequence[float],
        top_k: int,
    ) -> list[dict[str, Any]]:
        scope_sql, scope_args = self._scope_clause(knowledge_base_ids)
        sql = f"""
            SELECT chunk.id, chunk.document_id, chunk.knowledge_base_id, chunk.content,
                   chunk.page_start, chunk.page_end, chunk.section_path,
                   doc.original_filename,
                   1 - (chunk.embedding <=> %s::vector) AS score
            FROM rag_chunks chunk
            JOIN rag_documents doc ON doc.id = chunk.document_id
            JOIN rag_knowledge_bases kb ON kb.id = chunk.knowledge_base_id
            WHERE chunk.tenant_id = %s AND doc.status = 'completed'
              AND kb.status = 'active' AND chunk.index_version = kb.active_index_version
              {scope_sql}
            ORDER BY chunk.embedding <=> %s::vector
            LIMIT %s
        """
        vector = _vector_literal(query_embedding)
        args: list[Any] = [vector, tenant_id, *scope_args, vector, top_k]
        async with self.connection() as connection:
            rows = await (await connection.execute(sql, args)).fetchall()
            return [dict(row) for row in rows]

    async def sparse_search(
        self,
        *,
        tenant_id: UUID,
        knowledge_base_ids: Sequence[UUID],
        lexical_query: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        scope_sql, scope_args = self._scope_clause(knowledge_base_ids)
        sql = f"""
            WITH query AS (SELECT plainto_tsquery('simple'::regconfig, %s) AS value)
            SELECT chunk.id, chunk.document_id, chunk.knowledge_base_id, chunk.content,
                   chunk.page_start, chunk.page_end, chunk.section_path,
                   doc.original_filename,
                   ts_rank_cd(chunk.search_tsv, query.value) AS score
            FROM rag_chunks chunk
            JOIN rag_documents doc ON doc.id = chunk.document_id
            JOIN rag_knowledge_bases kb ON kb.id = chunk.knowledge_base_id
            CROSS JOIN query
            WHERE chunk.tenant_id = %s AND doc.status = 'completed'
              AND kb.status = 'active' AND chunk.index_version = kb.active_index_version
              AND chunk.search_tsv @@ query.value
              {scope_sql}
            ORDER BY score DESC
            LIMIT %s
        """
        args: list[Any] = [lexical_query, tenant_id, *scope_args, top_k]
        async with self.connection() as connection:
            rows = await (await connection.execute(sql, args)).fetchall()
            return [dict(row) for row in rows]


database = RAGDatabase()


async def initialize_database() -> None:
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    await database.open()
    await database.migrate()


async def shutdown_database() -> None:
    await database.close()
