"""Durable Agent run execution and PostgreSQL finalization."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.agent.main_agent import run_deep_agent
from app.api.monitor import monitor
from app.chat.db import database as chat_database
from app.core.redis import broker


OUTPUT_ROOT = Path(__file__).parents[1] / "output"


def scan_output_dir(path: str) -> list[dict[str, Any]]:
    """Return downloadable artifacts while enforcing the output-root boundary."""
    try:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(OUTPUT_ROOT.resolve()) or not resolved.exists():
            return []
    except Exception:
        return []

    files: list[dict[str, Any]] = []
    for file_path in resolved.rglob("*"):
        if not file_path.is_file():
            continue
        stat = file_path.stat()
        files.append(
            {
                "name": file_path.name,
                "type": "file",
                "path": str(file_path),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            }
        )
    return sorted(files, key=lambda item: item["mtime"], reverse=True)


def extract_final_answer(events: list[dict[str, Any]]) -> str:
    """Extract the terminal user-facing response from a normalized event list."""
    for event in reversed(events):
        name = event.get("event")
        if name == "task_result":
            data = event.get("data") or {}
            result = data.get("result")
            return result if isinstance(result, str) and result else event.get("message", "")
        if name == "task_cancelled":
            return event.get("message", "任务已取消")
        if name == "error":
            return event.get("message", "任务执行异常")
    return ""


def terminal_status(events: list[dict[str, Any]], default: str = "completed") -> str:
    names = {event.get("event") for event in events}
    if "task_cancelled" in names:
        return "cancelled"
    if "error" in names:
        return "failed"
    return default


async def _cancel_before_start(
    *, run_id: str, thread_id: str, worker_id: str
) -> str:
    changed = await chat_database.update_run_status(
        run_id, "cancelled", worker_id=worker_id
    )
    if not changed:
        current = await chat_database.get_run(run_id)
        current_status = str(current["status"]) if current else "missing"
        if current_status in TERMINAL_RUN_STATUSES:
            if broker.enabled:
                await broker.set_status(
                    run_id, current_status, worker_id=worker_id
                )
                await broker.clear_active_run(thread_id, run_id)
            return current_status
    if broker.enabled:
        await broker.set_status(run_id, "cancelled", worker_id=worker_id)
        await broker.publish_event(
            run_id,
            broker.build_event(
                "run_cancelled",
                "任务在进入执行阶段前已取消",
                run_id=run_id,
                thread_id=thread_id,
                data={"status": "cancelled"},
            ),
        )
        await broker.clear_active_run(thread_id, run_id)
    return "cancelled"


async def execute_run(
    *,
    query: str,
    thread_id: str,
    run_id: str,
    worker_id: str,
    tenant_id: str,
) -> str:
    """Execute one run and persist a terminal result exactly once."""
    if broker.enabled and await broker.cancel_requested(run_id):
        return await _cancel_before_start(
            run_id=run_id, thread_id=thread_id, worker_id=worker_id
        )

    started = await chat_database.update_run_status(
        run_id, "running", worker_id=worker_id
    )
    if not started:
        current = await chat_database.get_run(run_id)
        current_status = str(current["status"]) if current else "missing"
        if current_status == "cancelling":
            return await _cancel_before_start(
                run_id=run_id, thread_id=thread_id, worker_id=worker_id
            )
        if current_status in {"completed", "failed", "cancelled"}:
            return current_status
        raise RuntimeError(f"Run {run_id} cannot start from status {current_status}")
    if broker.enabled:
        await broker.set_status(run_id, "running", worker_id=worker_id)
        await broker.publish_event(
            run_id,
            broker.build_event(
                "run_started",
                "Agent Worker 已开始执行任务",
                run_id=run_id,
                thread_id=thread_id,
                data={"status": "running", "worker_id": worker_id},
            ),
        )

    execution_error: str | None = None
    cancelled = False
    try:
        await run_deep_agent(query, thread_id, run_id, tenant_id)
    except asyncio.CancelledError:
        cancelled = True
    except Exception as error:
        execution_error = str(error)
    finally:
        await broker.flush()

    events = monitor.drain(run_id)
    status = "cancelled" if cancelled else terminal_status(events)
    if execution_error:
        status = "failed"

    if await chat_database.conversation_exists(thread_id):
        content = extract_final_answer(events)
        session_dir = ""
        for event in events:
            if event.get("event") == "session_created":
                path = (event.get("data") or {}).get("path")
                if isinstance(path, str):
                    session_dir = path
        await chat_database.add_message(
            thread_id,
            "assistant",
            content,
            events=events,
            files=scan_output_dir(session_dir) if session_dir else [],
        )

    await chat_database.update_run_status(
        run_id,
        status,
        worker_id=worker_id,
        error_message=execution_error,
    )
    if broker.enabled:
        await broker.set_status(
            run_id,
            status,
            worker_id=worker_id,
            error=execution_error,
        )
        await broker.publish_event(
            run_id,
            broker.build_event(
                f"run_{status}",
                {
                    "completed": "任务执行完成",
                    "cancelled": "任务已取消",
                    "failed": "任务执行失败",
                }[status],
                run_id=run_id,
                thread_id=thread_id,
                data={"status": status, "error": execution_error},
            ),
        )
        await broker.clear_active_run(thread_id, run_id)
    return status
