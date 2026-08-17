"""
后台任务登记表

active_tasks 保存 thread_id -> 后台 Agent 任务，用于同一会话任务替换和主动取消。
独立成模块是为了让 server.py（写入）和 chat_api.py（删除会话时取消任务）
共享同一份登记表，避免循环 import。
"""

import asyncio

# 保存 thread_id -> 后台 Agent 任务
active_tasks: dict[str, asyncio.Task] = {}


def forget_task(thread_id: str, task: asyncio.Task) -> None:
    """
    清理已结束任务的登记关系。

    done_callback 触发时，active_tasks 中可能已经被新任务替换；只有仍是同一个
    task 时才删除，避免误清理同 thread_id 下刚启动的新任务。
    """
    if active_tasks.get(thread_id) is task:
        active_tasks.pop(thread_id, None)
