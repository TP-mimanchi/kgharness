"""Durable Agent execution and persistence, independent of the HTTP process."""

from __future__ import annotations

from pathlib import Path

from app.agent.main_agent import run_deep_agent
from app.api.monitor import monitor
from app.chat.db import database as chat_database

APP_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = APP_ROOT / "output"


def _scan_output_dir(path: str) -> list[dict]:
    try:
        absolute = Path(path).resolve()
        if not absolute.is_relative_to(OUTPUT_DIR.resolve()) or not absolute.exists():
            return []
    except Exception:
        return []
    files = []
    for file_path in absolute.rglob("*"):
        if file_path.is_file():
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


def _extract_final_answer(events: list[dict]) -> str:
    for event in reversed(events):
        event_name = event.get("event")
        if event_name == "task_result":
            return (event.get("data") or {}).get("result") or event.get("message", "")
        if event_name == "task_cancelled":
            return event.get("message", "任务已取消")
        if event_name == "error":
            return event.get("message", "任务执行异常")
    return ""


async def _persist_agent_events(thread_id: str) -> None:
    events = monitor.drain(thread_id)
    if not events or not await chat_database.conversation_exists(thread_id):
        return
    session_dir = ""
    for event in events:
        if event.get("event") == "session_created":
            value = (event.get("data") or {}).get("path")
            if isinstance(value, str):
                session_dir = value
    await chat_database.add_message(
        thread_id,
        "assistant",
        _extract_final_answer(events),
        events=events,
        files=_scan_output_dir(session_dir) if session_dir else [],
    )


async def execute_and_persist_agent(query: str, thread_id: str) -> None:
    """Run an Agent task and persist events without suppressing execution errors."""
    try:
        await run_deep_agent(query, thread_id)
    except BaseException:
        await _persist_agent_events(thread_id)
        raise
    else:
        await _persist_agent_events(thread_id)
