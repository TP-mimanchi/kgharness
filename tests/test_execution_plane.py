from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from app.core.config import ExecutionSettings
from app.core.redis import RunBroker
from app.services.agent_execution import extract_final_answer, terminal_status
from app.agent.main_agent import _stream_usage


def test_distributed_mode_requires_redis_url(monkeypatch) -> None:
    monkeypatch.setenv("RUN_EXECUTION_MODE", "distributed")
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(ValueError, match="REDIS_URL is required"):
        ExecutionSettings.from_env()


def test_execution_settings_validate_heartbeat(monkeypatch) -> None:
    monkeypatch.setenv("RUN_EXECUTION_MODE", "local")
    monkeypatch.setenv("AGENT_WORKER_HEARTBEAT_SECONDS", "0")

    with pytest.raises(ValueError, match="AGENT_WORKER_HEARTBEAT_SECONDS"):
        ExecutionSettings.from_env()


def test_execution_settings_validate_cancel_poll(monkeypatch) -> None:
    monkeypatch.setenv("RUN_EXECUTION_MODE", "local")
    monkeypatch.setenv("AGENT_CANCEL_POLL_SECONDS", "0.01")

    with pytest.raises(ValueError, match="AGENT_CANCEL_POLL_SECONDS"):
        ExecutionSettings.from_env()


def test_run_event_contract_contains_trace_identity() -> None:
    event = RunBroker.build_event(
        "node_completed",
        "节点完成",
        run_id="run-1",
        thread_id="thread-1",
        data={"node": "rag_agent"},
    )

    assert event["type"] == "monitor_event"
    assert event["run_id"] == "run-1"
    assert event["thread_id"] == "thread-1"
    assert event["event_id"]
    assert event["data"] == {"node": "rag_agent"}
    datetime.fromisoformat(event["timestamp"])


def test_stream_usage_normalizes_provider_metadata() -> None:
    class Chunk:
        usage_metadata = {"input_tokens": 120, "output_tokens": 35}
        response_metadata = {}

    assert _stream_usage(Chunk()) == (120, 35)


def test_stream_usage_supports_openai_token_usage() -> None:
    class Chunk:
        usage_metadata = None
        response_metadata = {
            "token_usage": {"prompt_tokens": 80, "completion_tokens": 20}
        }

    assert _stream_usage(Chunk()) == (80, 20)


def test_terminal_result_normalization() -> None:
    events = [
        {"event": "node_completed", "message": "完成"},
        {
            "event": "task_result",
            "message": "任务执行完成",
            "data": {"result": "企业级答案"},
        },
    ]

    assert extract_final_answer(events) == "企业级答案"
    assert terminal_status(events) == "completed"
    assert terminal_status([{"event": "task_cancelled"}]) == "cancelled"
    assert terminal_status([{"event": "error"}]) == "failed"


@pytest.mark.asyncio
async def test_run_event_publications_preserve_order(monkeypatch) -> None:
    broker = RunBroker()
    broker.client = object()  # type: ignore[assignment]
    published: list[int] = []

    async def publish(_run_id: str, payload: dict[str, int]) -> str:
        await asyncio.sleep((3 - payload["sequence"]) * 0.001)
        published.append(payload["sequence"])
        return str(payload["sequence"])

    monkeypatch.setattr(broker, "publish_event", publish)
    for sequence in range(3):
        broker.publish_event_nowait("run-1", {"sequence": sequence})
    await broker.flush()

    assert published == [0, 1, 2]
