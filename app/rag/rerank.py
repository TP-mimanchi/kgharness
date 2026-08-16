"""Alibaba Cloud Model Studio reranker with an explicit RRF fallback policy."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from app.rag.config import settings


class DashScopeReranker:
    """Call the Alibaba qwen3-rerank HTTP API without exposing API keys."""

    async def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[dict[str, Any]],
        top_k: int,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        if not candidates:
            return [], []
        if not settings.dashscope_api_key:
            return self._handle_failure(candidates, top_k, "阿里云百炼 API Key 未配置")

        payload = {
            "model": settings.rerank_model,
            "query": query,
            "documents": [candidate["content"] for candidate in candidates],
            "top_n": min(top_k, len(candidates)),
            "instruct": (
                "Given an enterprise knowledge-base question, retrieve passages "
                "that directly and factually answer the question."
            ),
        }
        try:
            async with httpx.AsyncClient(
                timeout=settings.rerank_timeout_seconds
            ) as client:
                response = await client.post(
                    settings.rerank_url,
                    headers={
                        "Authorization": f"Bearer {settings.dashscope_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
            results = body.get("results") or body.get("output", {}).get("results")
            if not isinstance(results, list):
                raise ValueError("阿里云 Rerank 响应缺少 results")
            reranked: list[dict[str, Any]] = []
            for result in results:
                index = int(result["index"])
                if not 0 <= index < len(candidates):
                    raise ValueError("阿里云 Rerank 返回了越界文档索引")
                item = dict(candidates[index])
                item["rerank_score"] = float(result["relevance_score"])
                reranked.append(item)
            return reranked[:top_k], []
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            return self._handle_failure(candidates, top_k, f"阿里云 Rerank 降级：{error}")

    @staticmethod
    def _handle_failure(
        candidates: Sequence[dict[str, Any]], top_k: int, message: str
    ) -> tuple[list[dict[str, Any]], list[str]]:
        if settings.rerank_failure_mode == "fail":
            raise RuntimeError(message)
        return [dict(candidate) for candidate in candidates[:top_k]], [message]


reranker = DashScopeReranker()
