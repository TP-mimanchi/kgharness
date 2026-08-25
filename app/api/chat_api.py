"""
历史会话管理接口

提供会话列表、会话消息查询和会话删除三个端点。会话 id 与 Agent 的 thread_id
一一对应：查询消息用于前端恢复聊天界面，删除会话会同步清理 LangGraph
checkpointer 中该线程的记忆，保证上下文不会残留。
"""

import asyncio
import uuid

from fastapi import APIRouter, HTTPException

from app.api.task_registry import active_tasks, forget_task
from app.chat.db import database
from app.core.config import settings as execution_settings
from app.core.redis import TERMINAL_RUN_STATUSES, broker
from app.services.run_cancellation import request_distributed_cancellation

router = APIRouter(prefix="/api/chats", tags=["chat-history"])


def _canonical_chat_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError) as error:
        raise HTTPException(status_code=400, detail="chat_id 必须是合法 UUID") from error


async def _cancel_active_task(thread_id: str) -> None:
    """
    删除会话前取消该 thread 上仍在执行的后台任务。

    取消后短暂等待协程响应；若底层同步调用阻塞，任务会稍后自行结束，
    其落库逻辑通过会话存在性检查避免把已删除的会话写回来。
    """
    active_run = await database.get_active_run(thread_id)
    if not active_run:
        return

    run_id = str(active_run["id"])
    if execution_settings.distributed:
        status = await request_distributed_cancellation(
            run_id=run_id,
            thread_id=thread_id,
            current_status=str(active_run["status"]),
        )
        if status == "cancelled":
            return
        for _ in range(20):
            await asyncio.sleep(0.25)
            current = await database.get_run(run_id)
            if not current or current["status"] in TERMINAL_RUN_STATUSES:
                return
        raise HTTPException(
            status_code=409,
            detail={"message": "任务正在取消，请稍后重试删除", "run_id": run_id},
        )

    task = active_tasks.get(thread_id)
    if not task or task.done():
        active_tasks.pop(thread_id, None)
        return
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except (asyncio.CancelledError, Exception):
        # CancelledError 继承自 BaseException，需单独捕获；其余异常一并吞掉
        pass
    forget_task(thread_id, task)


@router.get("")
async def list_chats():
    """按最近更新时间倒序返回全部历史会话。"""
    chats = await database.list_conversations()
    return {"chats": chats}


@router.get("/{chat_id}/messages")
async def list_chat_messages(chat_id: str):
    """返回指定会话的全部消息，供前端恢复聊天界面。"""
    chat_id = _canonical_chat_id(chat_id)
    if not await database.conversation_exists(chat_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    await database.finalize_stale_cancellations(chat_id)
    messages = await database.list_messages(chat_id)
    active_run = await database.get_active_run(chat_id)
    return {
        "messages": messages,
        "active_run": (
            {
                "id": str(active_run["id"]),
                "status": str(active_run["status"]),
                "execution_mode": execution_settings.mode,
            }
            if active_run
            else None
        ),
    }


@router.delete("/{chat_id}")
async def delete_chat(chat_id: str):
    """删除会话：先取消活跃任务，再删消息、会话和 checkpointer 记忆。"""
    chat_id = _canonical_chat_id(chat_id)
    if not await database.conversation_exists(chat_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    await _cancel_active_task(chat_id)
    await database.delete_conversation(chat_id)
    return {"status": "deleted", "thread_id": chat_id}
