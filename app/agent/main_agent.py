"""
主智能体组装与异步执行模块

负责把模型、主提示词、文件类工具和三个专家子智能体组装成 DeepAgent，
并提供 run_deep_agent 作为后续 API 层调用的统一入口。运行时还会为每个
session_id 创建独立工作目录，并把工具调用、子智能体调用和最终结果推送给前端。
"""

import asyncio
import shutil
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agent.llm import model
from app.agent.prompts import main_agent_content
from app.agent.subagents.database_query_agent import database_query_agent
from app.agent.subagents.knowledge_base_agent import knowledge_base_agent
from app.agent.subagents.network_search_agent import network_search_agent
from app.api.context import (
    reset_session_context,
    set_run_context,
    set_session_context,
    set_tenant_context,
    set_thread_context,
)
from app.api.monitor import monitor
from app.rag.config import settings as rag_settings

# 文件类工具由主智能体直接掌握，负责读取上传附件和生成最终交付文档
from app.tools.markdown_tools import generate_markdown
from app.tools.pdf_tools import convert_md_to_pdf
from app.tools.upload_file_read_tool import read_file_content

# 主智能体是调度中心：
# 1. tools 只放最终交付相关的文件工具
# 2. subagents 放网络、数据库、RAGFlow 三类信息获取助手
# 3. checkpointer 通过 thread_id 保存同一会话中的执行上下文
main_agent = None
_main_agent_lock = asyncio.Lock()
_checkpointer_context = None


async def initialize_main_agent():
    """Build the agent once with a durable PostgreSQL checkpointer."""
    global main_agent, _checkpointer_context
    if main_agent is not None:
        return main_agent
    async with _main_agent_lock:
        if main_agent is not None:
            return main_agent
        _checkpointer_context = AsyncPostgresSaver.from_conn_string(
            rag_settings.database_url
        )
        checkpointer = await _checkpointer_context.__aenter__()
        await checkpointer.setup()
        main_agent = create_deep_agent(
            model=model,
            system_prompt=main_agent_content["system_prompt"],
            tools=[generate_markdown, convert_md_to_pdf, read_file_content],
            checkpointer=checkpointer,
            subagents=[database_query_agent, network_search_agent, knowledge_base_agent],
        )
        return main_agent


async def shutdown_main_agent() -> None:
    global main_agent, _checkpointer_context
    main_agent = None
    if _checkpointer_context is not None:
        await _checkpointer_context.__aexit__(None, None, None)
        _checkpointer_context = None

# 当前文件位于 app/agent/main_agent.py，parents[1] 即 app 目录
project_root_path = Path(__file__).parents[1].resolve()


def _stream_content(message: Any) -> tuple[str, str]:
    """Extract public text and provider-supplied reasoning summaries from a chunk."""
    content = getattr(message, "content", "")
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    if isinstance(content, str):
        text_parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type", ""))
            if block_type in {"text", "text_delta", "output_text"}:
                value = block.get("text") or block.get("content")
                if isinstance(value, str):
                    text_parts.append(value)
            elif block_type in {"reasoning", "reasoning_delta", "reasoning_content"}:
                value = block.get("reasoning") or block.get("text") or block.get("content")
                if isinstance(value, str):
                    reasoning_parts.append(value)

    additional = getattr(message, "additional_kwargs", {}) or {}
    reasoning = additional.get("reasoning_content")
    if isinstance(reasoning, str):
        reasoning_parts.append(reasoning)
    return "".join(text_parts), "".join(reasoning_parts)


async def run_deep_agent(
    task_query: str,
    session_id: str,
    run_id: str | None = None,
    tenant_id: str | None = None,
):
    """
    异步流式执行主智能体

    API 层会为每次任务传入用户问题和 session_id。本函数负责准备会话目录、
    复制上传文件、写入 ContextVar，并在流式执行过程中把关键事件上报给前端。
    :param task_query: 前端提交的原始任务问题
    :param session_id: 当前任务 ID，同时用于 thread_id、输出目录和 WebSocket 定向推送
    """
    run_id = run_id or session_id
    print(f"[MainAgent] 开始执行会话，session_id={session_id}, run_id={run_id}")

    # 每个会话独立使用 output/session_{session_id}，避免不同用户的产物互相覆盖
    session_dir = project_root_path / "output" / f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)

    # 前端和工具使用绝对路径；提示词里只给模型相对路径，降低模型误用系统绝对路径的概率
    session_dir_str = str(session_dir).replace("\\", "/")
    relative_session_dir_str = str(session_dir.relative_to(project_root_path)).replace(
        "\\", "/"
    )

    # 上传文件先落在 updated/session_{session_id}，执行前复制到本次 output 工作目录
    # 这样读文件工具和生成文件工具都只需要围绕同一个 session_dir 工作
    updated_dir_path = project_root_path / "updated" / f"session_{session_id}"
    updated_info_prompt = ""
    if updated_dir_path.exists():
        files = [f.name for f in updated_dir_path.iterdir() if f.is_file()]
        if files:
            for filename in files:
                # copy2 会保留上传文件的修改时间、权限等元数据，便于后续排查文件来源
                shutil.copy2(updated_dir_path / filename, session_dir / filename)

            # 把上传文件列表注入用户消息，提醒模型先调用 read_file_content 获取附件内容
            updated_info_prompt = (
                "\n    [已上传文件] 已加载到工作目录:\n"
                + "\n".join([f"    - {f}" for f in files])
                + "\n    请优先使用工具（read_file_content）读取并参考这些文件。"
            )

    # ContextVar 让深层工具无需显式传参，也能拿到当前会话目录和 WebSocket thread_id
    session_dir_token = set_session_context(session_dir_str)
    session_id_token = set_thread_context(session_id)
    run_id_token = set_run_context(run_id)
    tenant_id_token = set_tenant_context(tenant_id or rag_settings.default_tenant_id)

    # 前端拿到工作目录后，可以展示本次任务生成的 Markdown/PDF 等产物
    monitor.report_session_dir(session_dir_str)

    # checkpointer 依赖 thread_id 区分会话记忆；同一 session_id 会复用同一条执行上下文
    config = {"configurable": {"thread_id": session_id}}

    # 工作环境指令是运行时动态补充的，约束模型只在当前会话目录读写文件
    path_instruction = f"""
    【工作环境指令】
    工作目录: {relative_session_dir_str}
    {updated_info_prompt}

    规则：
    1. 新生成文件必须保存到工作目录：'{relative_session_dir_str}/filename'
    2. 读取已上传的文件时，请直接将文件名（例如：'开篇.txt'）作为 filename 参数传入（read_file_content）读取工具，不要带上任何目录前缀。
    3. 使用相对路径，禁止使用绝对路径
    4. 若存在上传文件，请先分析内容
    """

    try:
        agent = await initialize_main_agent()
        last_answer = ""
        # messages 提供 token/content block 增量；updates 提供图节点状态变化。
        async for stream_item in agent.astream(
            {"messages": [{"role": "user", "content": task_query + path_instruction}]},
            config=config,
            stream_mode=["messages", "updates"],
        ):
            if not isinstance(stream_item, tuple) or len(stream_item) != 2:
                mode, chunk = "updates", stream_item
            else:
                mode, chunk = stream_item

            if mode == "messages" and isinstance(chunk, tuple) and len(chunk) == 2:
                message_chunk, metadata = chunk
                text_delta, reasoning_delta = _stream_content(message_chunk)
                node = metadata.get("langgraph_node") if isinstance(metadata, dict) else None
                if text_delta and node in {None, "model"}:
                    monitor.report_message_delta(
                        text_delta,
                        node=node,
                        message_id=getattr(message_chunk, "id", None),
                    )
                if reasoning_delta and node in {None, "model"}:
                    monitor.report_reasoning_delta(reasoning_delta, node=node)
                continue

            if mode != "updates" or not isinstance(chunk, dict):
                continue

            # update 形如 {"model": {"messages": [...]}}。
            for node_name, state in chunk.items():
                monitor.report_node_completed(str(node_name))
                if not state or "messages" not in state:
                    continue
                messages = state["messages"]
                if messages and isinstance(messages, list):
                    last_msg = messages[-1]
                    if node_name == "model":
                        monitor.report_model()
                        tool_calls = getattr(last_msg, "tool_calls", []) or []
                        if tool_calls:
                            # DeepAgents 调用子智能体时，本质上会产生名为 task 的工具调用
                            for tool_call in tool_calls:
                                if tool_call["name"] == "task":
                                    args = tool_call.get("args") or {}
                                    monitor.report_assistant(
                                        args.get("subagent_type", "unknown"),
                                        {
                                            "description": args.get("description", "")
                                        },
                                    )
                        else:
                            content, _ = _stream_content(last_msg)
                            if content:
                                last_answer = content

        if not last_answer:
            snapshot = await agent.aget_state(config)
            snapshot_messages = snapshot.values.get("messages", [])
            if snapshot_messages:
                last_answer, _ = _stream_content(snapshot_messages[-1])
        if last_answer:
            print(f"主智能体执行结果，最终结果：{last_answer[:100]}")
            monitor.report_task_result(last_answer)
        else:
            raise RuntimeError("Agent run completed without a final assistant response")

    except asyncio.CancelledError:
        monitor.report_task_cancelled()
        raise
    except Exception as e:
        # 异步执行异常也走 monitor，保证前端能收到明确错误事件
        monitor._emit("error", f"执行主智能发生异常信息：{str(e)}")
        raise
    finally:
        # 任务结束后恢复 ContextVar，避免后续请求复用到本次会话目录或 thread_id
        reset_session_context(
            session_dir_token,
            session_id_token,
            run_id_token,
            tenant_id_token,
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(
        run_deep_agent("从网络查询机器人信息，并生成Markdown文件", "test_session_001")
    )
