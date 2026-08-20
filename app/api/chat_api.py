"""
历史会话管理接口

提供会话列表、会话消息查询和会话删除三个端点。会话 id 与 Agent 的 thread_id
一一对应：查询消息用于前端恢复聊天界面，删除会话会同步清理 LangGraph
checkpointer 中该线程的记忆，保证上下文不会残留。
"""

import asyncio
import uuid

from fastapi import APIRouter, HTTPException

from app.chat.db import database
from app.core.redis import clear_active_task, get_active_task
from app.worker.celery_app import celery_app

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
    task_id = await get_active_task(thread_id)
    if not task_id:
        return
    await asyncio.to_thread(
        celery_app.control.revoke,
        task_id,
        terminate=True,
        signal="SIGTERM",
    )
    await clear_active_task(thread_id, task_id)


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
    messages = await database.list_messages(chat_id)
    return {"messages": messages}


@router.delete("/{chat_id}")
async def delete_chat(chat_id: str):
    """删除会话：先取消活跃任务，再删消息、会话和 checkpointer 记忆。"""
    chat_id = _canonical_chat_id(chat_id)
    if not await database.conversation_exists(chat_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    await _cancel_active_task(chat_id)
    await database.delete_conversation(chat_id)
    return {"status": "deleted", "thread_id": chat_id}
