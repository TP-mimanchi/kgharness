"""
FastAPI 接口层与项目闭环入口

负责承接前端的任务提交、任务取消、文件上传/下载、输出文件列表查询和
WebSocket 长连接。HTTP 接口只做轻量调度，真正的 DeepAgents 执行放到后台
任务中；执行进度、工具调用和最终结果由 monitor 按 thread_id 推送给前端。
"""

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

import uvicorn
import aiofiles
from fastapi import (
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.agent.main_agent import initialize_main_agent, shutdown_main_agent
from app.api.chat_api import router as chat_router
from app.api.monitor import manager
from app.api.rag_api import router as rag_router
from app.api.task_registry import active_tasks, forget_task
from app.chat.db import (
    ActiveRunExistsError,
    database as chat_database,
    initialize_chat_database,
    shutdown_chat_database,
)
from app.rag.db import initialize_database, shutdown_database
from app.core.config import settings as execution_settings
from app.core.redis import TERMINAL_RUN_STATUSES, broker
from app.rag.config import settings as rag_settings
from app.services.agent_execution import execute_run

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    服务生命周期入口。

    启动时绑定当前事件循环到 WebSocket 管理器，确保后台 Agent 任务可以把
    monitor 事件投递回 FastAPI 所在的 loop。
    """
    loop = asyncio.get_running_loop()
    manager.set_loop(loop)
    await initialize_database()
    await initialize_chat_database()
    await broker.connect(required=execution_settings.distributed)
    if not execution_settings.distributed:
        await initialize_main_agent()
    print(f"[Server] WebSocket Manager bound to loop: {id(loop)}")
    try:
        yield
    finally:
        if not execution_settings.distributed:
            await shutdown_main_agent()
        await broker.close()
        await shutdown_database()
        await shutdown_chat_database()


# 当前文件位于 app/api/server.py，运行时目录统一收敛到 app 目录
current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent

app = FastAPI(title="kgharness Enterprise Research API", lifespan=lifespan)
app.include_router(rag_router)
app.include_router(chat_router)

# output 保存每个会话最终工作区，前端只允许从这里浏览和下载生成文件
output_dir = project_root / "output"
output_dir.mkdir(exist_ok=True)

# updated 暂存用户上传文件，run_deep_agent 启动时会复制到对应 output/session_xxx
updated_dir = project_root / "updated"
updated_dir.mkdir(exist_ok=True)

# 通常前后端分别本地启动，这里放开跨域以便 Vite 页面直接调用 API
cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TaskRequest(BaseModel):
    """前端启动任务时提交的请求体。"""

    query: str = Field(min_length=1, max_length=12000)
    thread_id: str | None = None


def _canonical_thread_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError) as error:
        raise HTTPException(status_code=400, detail="thread_id 必须是合法 UUID") from error


@app.get("/health")
async def health():
    from app.rag.db import database

    healthy = await database.health()
    redis_healthy = await broker.health() if execution_settings.distributed else None
    if not healthy or (execution_settings.distributed and not redis_healthy):
        raise HTTPException(status_code=503, detail="database or Redis unavailable")
    return {
        "status": "ok",
        "database": "ok",
        "redis": "ok" if redis_healthy else ("disabled" if redis_healthy is None else "error"),
        "execution_mode": execution_settings.mode,
    }


@app.post("/api/task")
async def run_task(
    request: TaskRequest,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    """
    启动一次 DeepAgents 后台任务。

    HTTP 请求只负责创建后台协程并立即返回，后续执行轨迹、子智能体调用和最终
    答案都会由 monitor 通过 `/ws/{thread_id}` 推送给同一会话的前端。
    """
    thread_id = _canonical_thread_id(request.thread_id) if request.thread_id else str(uuid.uuid4())
    tenant_id = x_tenant_id or rag_settings.default_tenant_id
    try:
        tenant_id = str(uuid.UUID(tenant_id))
    except ValueError as error:
        raise HTTPException(status_code=400, detail="X-Tenant-ID 必须是合法 UUID") from error

    existing_run = await chat_database.get_active_run(thread_id)
    if existing_run:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "该会话已有执行中的任务",
                "run_id": str(existing_run["id"]),
                "status": existing_run["status"],
            },
        )

    run_id = str(uuid.uuid4())
    try:
        # 会话、用户消息和 durable run 必须在同一事务中成功。
        await chat_database.create_run_with_user_message(
            run_id, thread_id, tenant_id, request.query
        )
    except ActiveRunExistsError as error:
        active_run = await chat_database.get_active_run(thread_id)
        raise HTTPException(
            status_code=409,
            detail={
                "message": "该会话已有执行中的任务",
                "run_id": str(active_run["id"]) if active_run else None,
                "status": active_run["status"] if active_run else "active",
            },
        ) from error

    if execution_settings.distributed:
        try:
            await broker.enqueue_run(
                run_id=run_id,
                thread_id=thread_id,
                tenant_id=tenant_id,
                query=request.query,
            )
        except Exception as error:
            await chat_database.update_run_status(
                run_id, "failed", error_message=f"dispatch failed: {error}"
            )
            raise HTTPException(status_code=503, detail="任务队列暂不可用") from error
    else:
        task = asyncio.create_task(
            execute_run(
                query=request.query,
                thread_id=thread_id,
                run_id=run_id,
                worker_id="api-local",
                tenant_id=tenant_id,
            )
        )
        active_tasks[thread_id] = task
        task.add_done_callback(lambda finished_task: forget_task(thread_id, finished_task))

    return {
        "status": "started",
        "thread_id": thread_id,
        "run_id": run_id,
        "execution_mode": execution_settings.mode,
        "events_url": f"/api/runs/{run_id}/events",
    }


@app.post("/api/task/{thread_id}/cancel")
async def cancel_task(thread_id: str):
    """
    取消指定 thread_id 对应的后台 Agent 任务。

    注意：取消会向 asyncio.Task 注入 CancelledError。若底层第三方工具正在执行不可中断
    的同步阻塞调用，任务可能需要等该调用返回后才会真正结束。
    """
    thread_id = _canonical_thread_id(thread_id)
    active_run = await chat_database.get_active_run(thread_id)
    if not active_run:
        raise HTTPException(status_code=404, detail="任务不存在或已结束")
    run_id = str(active_run["id"])
    if execution_settings.distributed:
        await broker.request_cancel(run_id)
        await chat_database.update_run_status(run_id, "cancelling")
        return {"status": "cancelling", "thread_id": thread_id, "run_id": run_id}

    task = active_tasks.get(thread_id)
    if not task or task.done():
        active_tasks.pop(thread_id, None)
        raise HTTPException(status_code=404, detail="任务不存在或已结束")

    # 先发出取消信号，再短暂等待协程响应；若底层阻塞中，则返回 cancelling 给前端继续展示状态
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except asyncio.CancelledError:
        forget_task(thread_id, task)
        return {"status": "cancelled", "thread_id": thread_id, "run_id": run_id}
    except asyncio.TimeoutError:
        return {"status": "cancelling", "thread_id": thread_id, "run_id": run_id}
    except Exception as e:
        forget_task(thread_id, task)
        return {"status": "cancelled", "thread_id": thread_id, "run_id": run_id, "message": str(e)}

    forget_task(thread_id, task)
    return {"status": "cancelled", "thread_id": thread_id, "run_id": run_id}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    run_id = _canonical_thread_id(run_id)
    run = await chat_database.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run 不存在")
    return run


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    run_id = _canonical_thread_id(run_id)
    run = await chat_database.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run 不存在")
    if run["status"] in TERMINAL_RUN_STATUSES:
        raise HTTPException(status_code=409, detail="Run 已结束")
    if execution_settings.distributed:
        await broker.request_cancel(run_id)
        await chat_database.update_run_status(run_id, "cancelling")
        return {"status": "cancelling", "run_id": run_id, "thread_id": str(run["conversation_id"])}

    thread_id = str(run["conversation_id"])
    task = active_tasks.get(thread_id)
    if not task or task.done():
        raise HTTPException(status_code=404, detail="本地执行任务不存在或已结束")
    task.cancel()
    return {"status": "cancelling", "run_id": run_id, "thread_id": thread_id}


@app.get("/api/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    """Replay and follow a run's ordered Redis event stream using SSE."""
    run_id = _canonical_thread_id(run_id)
    run = await chat_database.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run 不存在")
    if not execution_settings.distributed or not broker.enabled:
        raise HTTPException(status_code=409, detail="SSE 事件流仅在 distributed 模式启用")

    async def event_generator():
        cursor = last_event_id or "0-0"
        while not await request.is_disconnected():
            events = await broker.read_events(run_id, cursor)
            if events:
                for event_id, payload in events:
                    cursor = event_id
                    yield f"id: {event_id}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
                    if payload.get("event") in {
                        "run_completed",
                        "run_failed",
                        "run_cancelled",
                    }:
                        return
                continue

            current = await chat_database.get_run(run_id)
            if current and current["status"] in TERMINAL_RUN_STATUSES:
                return
            yield ": heartbeat\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/upload")
async def upload_files(files: List[UploadFile] = File(...), thread_id: str = Form(...)):
    """
    文件上传接口 (File Upload)。

    目标：
    1. 接收用户上传的一个或多个文件。
    2. 保存到 `updated/session_{thread_id}` 目录。
    3. 供 Agent 在后续任务中读取和分析。

    Args:
        files (List[UploadFile]): 文件对象列表。
        thread_id (str): 关联的任务会话 ID。
    """
    # 上传文件先按会话隔离保存，避免不同任务读取到彼此的附件
    thread_id = _canonical_thread_id(thread_id)
    target_dir = updated_dir / f"session_{thread_id}"
    target_dir.mkdir(parents=True, exist_ok=True)

    saved_files = []
    for file in files:
        safe_name = Path(file.filename or "upload").name
        if not safe_name or safe_name in {".", ".."}:
            raise HTTPException(status_code=400, detail="无效文件名")
        file_path = target_dir / safe_name
        size = 0
        try:
            async with aiofiles.open(file_path, "wb") as buffer:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > 100 * 1024 * 1024:
                        raise HTTPException(status_code=413, detail="文件超过 100MB 限制")
                    await buffer.write(chunk)
        except Exception:
            file_path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()
        saved_files.append(safe_name)

    return {"status": "uploaded", "files": saved_files}


@app.get("/api/download")
async def download_file(path: str):
    """
    文件下载接口 (File Download)。

    目标：
    1. 根据绝对路径下载文件。
    2. 严格的安全检查，防止越权访问。

    Args:
        path (str): 文件的绝对路径 (通常从 list_files 接口获取)。
    """
    try:
        # resolve 后再做 is_relative_to，防止 `../` 之类的路径穿越到 output 之外
        abs_path = Path(path).resolve()
        output_abs = output_dir.resolve()

        if not abs_path.is_relative_to(output_abs):
            return {"error": "拒绝访问: 只能下载输出目录下的文件"}
    except Exception:
        return {"error": "无效的路径参数"}

    if not abs_path.exists():
        return {"error": "文件不存在"}

    # FileResponse 会以流式响应返回文件内容，并让浏览器使用原文件名下载
    return FileResponse(abs_path, filename=abs_path.name)


@app.get("/api/files")
async def list_files(path: str):
    """
    文件列表查询接口 (File Explorer)。

    目标：
    1. 列出指定目录下的所有生成文件。
    2. 提供文件元数据（大小、修改时间、下载所需路径）。
    3. 严格的安全检查，防止路径遍历攻击。

    Args:
        path (str): 目标目录的绝对路径 (必须在 output 目录下)。
    """
    print(f"[DEBUG] 请求文件列表: {path}")

    try:
        # 和下载接口保持同一条安全边界：前端只能查看 output 目录内部内容
        abs_path = Path(path).resolve()
        output_abs = output_dir.resolve()

        if not abs_path.is_relative_to(output_abs):
            print(f"[ERROR] 拒绝访问: {abs_path} 不在 {output_abs} 目录下")
            return {"error": "拒绝访问: 只能访问输出目录下的文件"}

    except Exception as e:
        print(f"[ERROR] 路径解析失败: {e}")
        return {"error": f"路径无效: {e}"}

    if not abs_path.exists():
        return {"error": "目录不存在"}

    files = []
    try:
        # 递归返回文件元数据，前端据此渲染文件列表并发起下载请求
        for file_path in abs_path.rglob("*"):
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

    except Exception as e:
        print(f"[ERROR] 遍历文件失败: {e}")
        return {"error": str(e)}

    # 最新生成的文件排在前面，方便用户优先看到本次任务产物
    files.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    print(f"[DEBUG] 找到 {len(files)} 个文件")
    return {"files": files}


@app.websocket("/ws/{thread_id}")
async def websocket_endpoint(websocket: WebSocket, thread_id: str):
    """
    WebSocket 实时通讯核心接口 (Real-time Communication)。

    连接建立后，ConnectionManager 会用 thread_id 保存 WebSocket。monitor 后续
    发送事件时只需要按 thread_id 查找连接，就能把进度推给对应页面。循环中的
    receive_text 用于接收前端心跳，避免连接空闲断开。
    """
    try:
        thread_id = str(uuid.UUID(thread_id))
    except ValueError:
        await websocket.close(code=1008, reason="thread_id 必须是合法 UUID")
        return
    origin = websocket.headers.get("origin")
    if origin and origin not in cors_origins:
        await websocket.close(code=1008, reason="WebSocket Origin 不允许")
        return
    print(f"会话向我们发起了请求，要求建立连接：{thread_id} 对应：{websocket}")

    # 连接建立后立即按 thread_id 注册，monitor 后续才能把事件定向推给当前页面
    await manager.connect(websocket, thread_id)

    try:
        while True:
            # 前端通常发送 ping 心跳；服务端回复 pong，顺便维持连接活跃
            data = await websocket.receive_text()
            await websocket.send_json(
                {"type": "pong", "message": f"服务端已收到: {data}"}
            )

    except WebSocketDisconnect:
        # 只移除当前 WebSocket 实例，避免旧连接断开时误删同 thread_id 的新连接
        manager.disconnect(websocket, thread_id)
        print(f"[WebSocket] 客户端已断开: {thread_id}")

    except Exception as e:
        print(f"[WebSocket] 连接异常: {e}")
        manager.disconnect(websocket, thread_id)


if __name__ == "__main__":
    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=True)
