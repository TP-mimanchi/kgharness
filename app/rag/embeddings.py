"""Embedding providers used by ingestion and retrieval."""

from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence

from openai import AsyncOpenAI

from app.rag.config import settings

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_\-\.]+|[\u3400-\u9fff]")


def lexicalize(text: str) -> str:
    """Create stable tokens for PostgreSQL simple FTS, including Chinese bigrams."""
    raw_tokens = TOKEN_PATTERN.findall(text.lower())
    tokens: list[str] = []
    chinese_buffer: list[str] = []

    def flush_chinese() -> None:
        if not chinese_buffer:
            return
        tokens.extend(chinese_buffer)
        if len(chinese_buffer) > 1:
            tokens.extend(
                chinese_buffer[index] + chinese_buffer[index + 1]
                for index in range(len(chinese_buffer) - 1)
            )
        chinese_buffer.clear()

    for token in raw_tokens:
        if len(token) == 1 and "\u3400" <= token <= "\u9fff":
            chinese_buffer.append(token)
        else:
            flush_chinese()
            tokens.append(token)
    flush_chinese()
    return " ".join(tokens)


class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError


class OpenAICompatibleEmbedding(EmbeddingProvider):
    def __init__(self) -> None:
        self.client = AsyncOpenAI(
            api_key=settings.dashscope_api_key,
            base_url=settings.dashscope_base_url,
            timeout=30.0,
            max_retries=2,
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        response = await self.client.embeddings.create(
            model=settings.embedding_model,
            input=list(texts),
            dimensions=settings.embedding_dimension,
        )
        vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
        for vector in vectors:
            if len(vector) != settings.embedding_dimension:
                raise ValueError(
                    f"embedding provider returned {len(vector)} dimensions; "
                    f"configured {settings.embedding_dimension}"
                )
        return vectors


class HashEmbedding(EmbeddingProvider):
    """Deterministic local embedding for tests and offline smoke checks only."""

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * settings.embedding_dimension
        for token in lexicalize(text).split():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
            index = int.from_bytes(digest[:8], "big") % settings.embedding_dimension
            sign = 1.0 if digest[8] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


def create_embedding_provider() -> EmbeddingProvider:
    if settings.embedding_provider == "hash":
        return HashEmbedding()
    return OpenAICompatibleEmbedding()


embedding_provider = create_embedding_provider()
