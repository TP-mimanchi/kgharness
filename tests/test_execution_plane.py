from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from app.core.config import ExecutionSettings
from app.core.errors import TransientRunError, is_transient_infrastructure_error
from app.core.postgres import create_postgres_pool
from app.core.redis import RunBroker
from app.services.agent_execution import extract_final_answer, terminal_status
from app.agent.llm import ModelUsageMonitor, _result_usage
from app.services.run_cancellation import request_distributed_cancellation
from app.agent.worker import _execute_claim


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


def test_execution_settings_require_worker_ttl_above_heartbeat(monkeypatch) -> None:
    monkeypatch.setenv("RUN_EXECUTION_MODE", "local")
    monkeypatch.setenv("AGENT_WORKER_HEARTBEAT_SECONDS", "10")
    monkeypatch.setenv("AGENT_WORKER_REGISTRY_TTL_SECONDS", "20")

    with pytest.raises(ValueError, match="must exceed twice"):
        ExecutionSettings.from_env()


def test_postgres_pool_rotates_and_checks_connections() -> None:
    pool = create_postgres_pool(
        "postgresql://user:password@localhost/database",
        name="test-pool",
        min_size=1,
        max_size=2,
    )

    assert pool.name == "test-pool"
    assert pool.min_size == 1
    assert pool.max_size == 2
    assert pool.max_lifetime > pool.max_idle


def test_closed_connection_is_classified_as_transient() -> None:
    assert is_transient_infrastructure_error(RuntimeError("the connection is closed"))
    assert not is_transient_infrastructure_error(ValueError("invalid tool argument"))


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


@pytest.mark.asyncio
async def test_broker_health_pings_connected_client() -> None:
    class Client:
        async def ping(self) -> bool:
            return True

    broker = RunBroker()
    broker.client = Client()  # type: ignore[assignment]

    assert await broker.health() is True


def test_result_usage_normalizes_provider_metadata() -> None:
    class Message:
        usage_metadata = {"input_tokens": 120, "output_tokens": 35}
        response_metadata = {"model_name": "qwen-max"}

    class Generation:
        message = Message()

    class Result:
        generations = [[Generation()]]
        llm_output = None

    assert _result_usage(Result()) == (120, 35, "qwen-max")  # type: ignore[arg-type]


def test_result_usage_supports_openai_token_usage() -> None:
    class Result:
        generations = []
        llm_output = {
            "token_usage": {"prompt_tokens": 80, "completion_tokens": 20}
        }

    assert _result_usage(Result()) == (80, 20, None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_usage_callback_reports_one_model_call(monkeypatch) -> None:
    reported: list[dict[str, object]] = []

    class Result:
        generations = []
        llm_output = {
            "token_usage": {"prompt_tokens": 80, "completion_tokens": 20},
            "model_name": "qwen-max",
        }

    monkeypatch.setattr(
        "app.agent.llm.monitor.report_model_usage",
        lambda **kwargs: reported.append(kwargs),
    )
    callback = ModelUsageMonitor()
    await callback.on_llm_end(Result(), run_id=__import__("uuid").uuid4())  # type: ignore[arg-type]

    assert len(reported) == 1
    assert reported[0]["input_tokens"] == 80
    assert reported[0]["output_tokens"] == 20


@pytest.mark.asyncio
async def test_queued_distributed_run_is_cancelled_immediately(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    async def request_cancel(run_id: str) -> None:
        calls.append(("request_cancel", run_id))

    async def update_run_status(run_id: str, status: str, **_kwargs) -> bool:
        calls.append(("database_status", status))
        return True

    async def set_status(run_id: str, status: str, **_kwargs) -> None:
        calls.append(("redis_status", status))

    async def publish_event(run_id: str, _payload: dict) -> str:
        calls.append(("publish", run_id))
        return "1-0"

    async def clear_active_run(thread_id: str, run_id: str) -> None:
        calls.append(("clear_active", f"{thread_id}:{run_id}"))

    monkeypatch.setattr("app.services.run_cancellation.broker.request_cancel", request_cancel)
    monkeypatch.setattr("app.services.run_cancellation.database.update_run_status", update_run_status)
    monkeypatch.setattr("app.services.run_cancellation.broker.set_status", set_status)
    monkeypatch.setattr("app.services.run_cancellation.broker.publish_event", publish_event)
    monkeypatch.setattr("app.services.run_cancellation.broker.clear_active_run", clear_active_run)

    status = await request_distributed_cancellation(
        run_id="run-1",
        thread_id="thread-1",
        current_status="queued",
    )

    assert status == "cancelled"
    assert ("database_status", "cancelled") in calls
    assert ("redis_status", "cancelled") in calls
    assert any(name == "clear_active" for name, _ in calls)


@pytest.mark.asyncio
async def test_worker_acks_database_terminal_delivery(monkeypatch) -> None:
    calls: list[str] = []

    async def get_status(_run_id: str) -> dict[str, str]:
        return {"status": "cancelling"}

    async def get_run(_run_id: str) -> dict[str, str]:
        return {"status": "cancelled"}

    async def set_status(_run_id: str, status: str, **_kwargs) -> None:
        calls.append(f"status:{status}")

    async def clear_active_run(_thread_id: str, _run_id: str) -> None:
        calls.append("clear")

    async def acknowledge(_message_id: str) -> None:
        calls.append("ack")

    monkeypatch.setattr("app.agent.worker.broker.get_status", get_status)
    monkeypatch.setattr("app.agent.worker.chat_database.get_run", get_run)
    monkeypatch.setattr("app.agent.worker.broker.set_status", set_status)
    monkeypatch.setattr("app.agent.worker.broker.clear_active_run", clear_active_run)
    monkeypatch.setattr("app.agent.worker.broker.acknowledge", acknowledge)

    await _execute_claim(
        "worker-1",
        "1-0",
        {
            "run_id": "run-1",
            "thread_id": "thread-1",
            "query": "hello",
            "tenant_id": "tenant-1",
        },
    )

    assert calls == ["status:cancelled", "clear", "ack"]


@pytest.mark.asyncio
async def test_worker_requeues_transient_failure_with_same_run_id(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    get_run_calls = 0

    async def get_status(_run_id: str) -> dict[str, str]:
        return {"status": "queued"}

    async def get_run(_run_id: str) -> dict[str, str]:
        nonlocal get_run_calls
        get_run_calls += 1
        if get_run_calls == 1:
            return {"status": "queued", "worker_id": ""}
        return {"status": "running", "worker_id": "worker-1"}

    async def execute_run(**_kwargs) -> str:
        raise TransientRunError("the connection is closed")

    async def reschedule_run(_run_id: str, **_kwargs) -> int:
        calls.append(("rescheduled", 1))
        return 1

    async def requeue_run(**kwargs) -> str:
        calls.append(("requeued_run_id", kwargs["run_id"]))
        calls.append(("attempt", kwargs["attempt"]))
        return "2-0"

    async def acknowledge(message_id: str) -> None:
        calls.append(("ack", message_id))

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.agent.worker.broker.get_status", get_status)
    monkeypatch.setattr("app.agent.worker.chat_database.get_run", get_run)
    monkeypatch.setattr("app.agent.worker.execute_run", execute_run)
    monkeypatch.setattr("app.agent.worker.chat_database.reschedule_run", reschedule_run)
    monkeypatch.setattr("app.agent.worker.broker.requeue_run", requeue_run)
    monkeypatch.setattr("app.agent.worker.broker.acknowledge", acknowledge)
    monkeypatch.setattr("app.agent.worker.asyncio.sleep", no_sleep)

    await _execute_claim(
        "worker-1",
        "1-0",
        {
            "run_id": "run-1",
            "thread_id": "thread-1",
            "query": "hello",
            "tenant_id": "tenant-1",
            "attempt": "0",
            "_delivery_kind": "new",
        },
    )

    assert ("requeued_run_id", "run-1") in calls
    assert ("attempt", 1) in calls
    assert ("ack", "1-0") in calls


@pytest.mark.asyncio
async def test_worker_executes_distinct_conversations_concurrently(monkeypatch) -> None:
    started_threads: set[str] = set()
    both_started = asyncio.Event()
    release_runs = asyncio.Event()
    acknowledged: list[str] = []

    async def get_status(_run_id: str) -> dict[str, str]:
        return {"status": "queued"}

    async def get_run(_run_id: str) -> dict[str, str]:
        return {"status": "queued", "worker_id": ""}

    async def execute_run(**kwargs) -> str:
        started_threads.add(str(kwargs["thread_id"]))
        if len(started_threads) == 2:
            both_started.set()
        await release_runs.wait()
        return "completed"

    async def acknowledge(message_id: str) -> None:
        acknowledged.append(message_id)

    monkeypatch.setattr("app.agent.worker.broker.get_status", get_status)
    monkeypatch.setattr("app.agent.worker.chat_database.get_run", get_run)
    monkeypatch.setattr("app.agent.worker.execute_run", execute_run)
    monkeypatch.setattr("app.agent.worker.broker.acknowledge", acknowledge)

    claims = [
        _execute_claim(
            f"worker-1:{index}",
            f"{index + 1}-0",
            {
                "run_id": f"run-{index + 1}",
                "thread_id": f"thread-{index + 1}",
                "query": "hello",
                "tenant_id": "tenant-1",
            },
        )
        for index in range(2)
    ]
    tasks = [asyncio.create_task(claim) for claim in claims]
    try:
        await asyncio.wait_for(both_started.wait(), timeout=1)
        assert started_threads == {"thread-1", "thread-2"}
    finally:
        release_runs.set()
        await asyncio.gather(*tasks)

    assert set(acknowledged) == {"1-0", "2-0"}


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
