"""RAG configuration with validation and safe defaults for the pilot deployment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())


DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    value = int(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class RAGSettings:
    database_url: str
    storage_dir: Path
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int
    embedding_batch_size: int
    dashscope_api_key: str | None
    dashscope_base_url: str
    rerank_model: str
    rerank_url: str
    rerank_candidate_k: int
    rerank_timeout_seconds: float
    rerank_failure_mode: str
    chunk_size: int
    chunk_overlap: int
    dense_top_k: int
    sparse_top_k: int
    final_top_k: int
    max_upload_bytes: int
    worker_poll_seconds: float
    default_tenant_id: str

    @classmethod
    def from_env(cls) -> "RAGSettings":
        storage_dir = Path(os.getenv("RAG_STORAGE_DIR", "app/rag_data")).resolve()
        provider = os.getenv("RAG_EMBEDDING_PROVIDER", "openai").strip().lower()
        if provider not in {"openai", "hash"}:
            raise ValueError("RAG_EMBEDDING_PROVIDER must be 'openai' or 'hash'")
        dashscope_base_url = os.getenv(
            "DASHSCOPE_BASE_URL",
            os.getenv(
                "OPENAI_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
        ).rstrip("/")
        rerank_model = os.getenv("RAG_RERANK_MODEL", "qwen3-rerank")
        rerank_url = os.getenv("DASHSCOPE_RERANK_URL")
        if not rerank_url:
            origin = dashscope_base_url.split("/compatible-mode/", 1)[0]
            rerank_url = (
                f"{origin}/compatible-api/v1/reranks"
                if rerank_model == "qwen3-rerank"
                else f"{origin}/api/v1/services/rerank/text-rerank/text-rerank"
            )
        rerank_failure_mode = os.getenv(
            "RAG_RERANK_FAILURE_MODE", "fallback"
        ).strip().lower()
        if rerank_failure_mode not in {"fallback", "fail"}:
            raise ValueError("RAG_RERANK_FAILURE_MODE must be 'fallback' or 'fail'")
        return cls(
            database_url=os.getenv(
                "RAG_DATABASE_URL",
                "postgresql://kgharness:kgharness@localhost:5432/kgharness",
            ),
            storage_dir=storage_dir,
            embedding_provider=provider,
            embedding_model=os.getenv("RAG_EMBEDDING_MODEL", "text-embedding-v3"),
            embedding_dimension=_int_env("RAG_EMBEDDING_DIMENSION", 1024, 64, 4096),
            # Alibaba text-embedding-v3/v4 accepts at most 10 texts per request.
            embedding_batch_size=_int_env("RAG_EMBEDDING_BATCH_SIZE", 10, 1, 10),
            dashscope_api_key=os.getenv("DASHSCOPE_API_KEY")
            or os.getenv("OPENAI_API_KEY"),
            dashscope_base_url=dashscope_base_url,
            rerank_model=rerank_model,
            rerank_url=rerank_url,
            rerank_candidate_k=_int_env("RAG_RERANK_CANDIDATE_K", 20, 1, 100),
            rerank_timeout_seconds=float(
                os.getenv("RAG_RERANK_TIMEOUT_SECONDS", "20")
            ),
            rerank_failure_mode=rerank_failure_mode,
            chunk_size=_int_env("RAG_CHUNK_SIZE", 512, 128, 2048),
            chunk_overlap=_int_env("RAG_CHUNK_OVERLAP", 64, 0, 512),
            dense_top_k=_int_env("RAG_DENSE_TOP_K", 30, 1, 100),
            sparse_top_k=_int_env("RAG_SPARSE_TOP_K", 30, 1, 100),
            final_top_k=_int_env("RAG_FINAL_TOP_K", 8, 1, 30),
            max_upload_bytes=_int_env(
                "RAG_MAX_UPLOAD_BYTES", 100 * 1024 * 1024, 1024, 1024 * 1024 * 1024
            ),
            worker_poll_seconds=float(os.getenv("RAG_WORKER_POLL_SECONDS", "1.0")),
            default_tenant_id=os.getenv("RAG_DEFAULT_TENANT_ID", DEFAULT_TENANT_ID),
        )


settings = RAGSettings.from_env()
