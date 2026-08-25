"""
大模型初始化模块

负责从 .env 中读取模型配置，并创建项目统一复用的模型对象
后续主智能体和子智能体都从这里导入 model，避免在多个文件里重复加载环境变量
"""

import os
from typing import Any
from uuid import UUID

from dotenv import find_dotenv, load_dotenv
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult
from langchain_openai import ChatOpenAI

from app.api.monitor import monitor

load_dotenv(find_dotenv())


def _usage_values(value: Any) -> tuple[int, int] | None:
    """Normalize LangChain and OpenAI-compatible usage dictionaries."""
    if not isinstance(value, dict):
        return None
    input_tokens = value.get("input_tokens", value.get("prompt_tokens", 0))
    output_tokens = value.get("output_tokens", value.get("completion_tokens", 0))
    try:
        normalized = (int(input_tokens or 0), int(output_tokens or 0))
    except (TypeError, ValueError):
        return None
    return normalized if sum(normalized) > 0 else None


def _result_usage(response: LLMResult) -> tuple[int, int, str | None] | None:
    """Read one completed provider call's usage from a LangChain result."""
    input_tokens = 0
    output_tokens = 0
    model_name: str | None = None
    found_generation_usage = False

    for generation_group in response.generations:
        for generation in generation_group:
            message = getattr(generation, "message", None)
            usage = _usage_values(getattr(message, "usage_metadata", None))
            response_metadata = getattr(message, "response_metadata", {}) or {}
            if usage is None:
                usage = _usage_values(
                    response_metadata.get("token_usage")
                    or response_metadata.get("usage")
                )
            if usage:
                found_generation_usage = True
                input_tokens += usage[0]
                output_tokens += usage[1]
            if not model_name:
                raw_model_name = response_metadata.get("model_name") or response_metadata.get(
                    "model"
                )
                if raw_model_name:
                    model_name = str(raw_model_name)

    llm_output = response.llm_output or {}
    if not found_generation_usage:
        usage = _usage_values(llm_output.get("token_usage") or llm_output.get("usage"))
        if usage:
            input_tokens, output_tokens = usage
    if not model_name:
        raw_model_name = llm_output.get("model_name") or llm_output.get("model")
        if raw_model_name:
            model_name = str(raw_model_name)

    if input_tokens + output_tokens == 0:
        return None
    return input_tokens, output_tokens, model_name


class ModelUsageMonitor(AsyncCallbackHandler):
    """Capture every completed call made through the shared main/subagent model."""

    async def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        del parent_run_id, kwargs
        usage = _result_usage(response)
        if usage:
            monitor.report_model_usage(
                call_id=str(run_id),
                input_tokens=usage[0],
                output_tokens=usage[1],
                model_name=usage[2],
            )

# 仅复用 OpenAI 兼容协议；实际服务、模型和密钥均来自阿里云百炼。
model = ChatOpenAI(
    model=os.getenv("LLM_QWEN_MAX", "qwen-max"),
    api_key=os.getenv("DASHSCOPE_API_KEY", os.getenv("OPENAI_API_KEY")),
    base_url=os.getenv("DASHSCOPE_BASE_URL", os.getenv("OPENAI_BASE_URL")),
    timeout=60,
    max_retries=2,
    streaming=True,
    stream_usage=True,
    callbacks=[ModelUsageMonitor()],
)
