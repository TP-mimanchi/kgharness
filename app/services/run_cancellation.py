"""Shared cancellation transitions for distributed Agent runs."""

from __future__ import annotations

from app.chat.db import database
from app.core.redis import broker


async def request_distributed_cancellation(
    *,
    run_id: str,
    thread_id: str,
    current_status: str,
) -> str:
    """Cancel queued work immediately; running work transitions through cancelling."""
    await broker.request_cancel(run_id)
    if current_status != "queued":
        await database.update_run_status(run_id, "cancelling")
        return "cancelling"

    # No worker has started a queued run, so retaining an active DB lock only blocks
    # the next message. The queue consumer will see the terminal Redis state and ack it.
    await database.update_run_status(run_id, "cancelled")
    await broker.set_status(run_id, "cancelled")
    await broker.publish_event(
        run_id,
        broker.build_event(
            "run_cancelled",
            "排队任务已取消",
            run_id=run_id,
            thread_id=thread_id,
            data={"status": "cancelled", "phase": "queued"},
        ),
    )
    await broker.clear_active_run(thread_id, run_id)
    return "cancelled"
