"""Export the live FastAPI contract as an Apifox-ready OpenAPI document."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api.server import app  # noqa: E402


OUTPUT = ROOT / "docs" / "api" / "kgharness-apifox-openapi.json"
REMOTE_HTTP = "http://119.91.123.102"
REMOTE_WS = "ws://119.91.123.102"
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"
EXAMPLE_THREAD_ID = "7b1f3b92-9d41-4e76-a6bf-a27877c75f09"
EXAMPLE_KB_ID = "8b3958ac-d0b2-4abc-af3b-38d2bf1d9f61"
EXAMPLE_DOCUMENT_ID = "acb46bca-1568-40df-9d50-a243ac081e84"
EXAMPLE_JOB_ID = "24d45f36-adfe-45d7-9561-3d8101689eb9"


OPERATION_METADATA: dict[tuple[str, str], tuple[str, str, str]] = {
    ("GET", "/health"): ("系统健康检查", "检查 API 与 PostgreSQL 是否可用。", "系统"),
    ("GET", "/api/v1/rag/health"): ("RAG 健康检查", "检查 RAG 数据库连接。", "RAG 系统"),
    ("POST", "/api/task"): (
        "启动智能体任务",
        "异步启动主智能体任务。应先建立同 thread_id 的 WebSocket 连接，再调用本接口接收执行事件。",
        "智能体任务",
    ),
    ("POST", "/api/task/{thread_id}/cancel"): (
        "取消智能体任务",
        "取消指定 UUID 会话中正在执行的任务。",
        "智能体任务",
    ),
    ("POST", "/api/upload"): (
        "上传智能体附件",
        "上传一个或多个附件到指定 UUID 会话，单文件最大 100 MB。",
        "智能体文件",
    ),
    ("GET", "/api/files"): (
        "查询任务输出文件",
        "递归列出任务输出目录中的文件。path 必须位于服务端 output 目录内。",
        "智能体文件",
    ),
    ("GET", "/api/download"): (
        "下载任务输出文件",
        "下载 /api/files 返回的文件路径；服务端会阻止 output 目录之外的访问。",
        "智能体文件",
    ),
    ("POST", "/api/v1/knowledge-bases"): (
        "创建知识库",
        "在当前租户下创建知识库，名称在租户内唯一。",
        "企业 RAG",
    ),
    ("GET", "/api/v1/knowledge-bases"): (
        "查询知识库列表",
        "查询当前租户的全部知识库。",
        "企业 RAG",
    ),
    ("POST", "/api/v1/knowledge-bases/{knowledge_base_id}/documents"): (
        "上传知识库文档",
        "上传文档并创建异步摄取任务。支持 PDF、DOCX、XLSX、CSV、Markdown、TXT 和 HTML。",
        "企业 RAG",
    ),
    ("GET", "/api/v1/documents/{document_id}"): (
        "查询文档状态",
        "查询知识库文档的元数据与处理状态。",
        "企业 RAG",
    ),
    ("GET", "/api/v1/ingestion-jobs/{job_id}"): (
        "查询摄取任务",
        "查询解析、切分、向量化和入库进度。",
        "企业 RAG",
    ),
    ("POST", "/api/v1/ingestion-jobs/{job_id}/retry"): (
        "重试摄取任务",
        "仅失败状态的摄取任务可以重试。",
        "企业 RAG",
    ),
    ("POST", "/api/v1/retrieval/search"): (
        "混合检索",
        "执行 Dense + PostgreSQL 全文检索、RRF 融合与阿里云 Rerank，返回可引用证据。",
        "企业 RAG",
    ),
}


REQUEST_EXAMPLES: dict[tuple[str, str], Any] = {
    ("POST", "/api/task"): {
        "query": "结合知识库证据，分析企业级 RAG 的实施风险并给出建议。",
        "thread_id": EXAMPLE_THREAD_ID,
    },
    ("POST", "/api/v1/knowledge-bases"): {
        "name": "企业制度知识库",
        "description": "用于内部制度、流程与规范检索",
    },
    ("POST", "/api/v1/retrieval/search"): {
        "query": "文档中对数据留存期限有哪些规定？",
        "knowledge_base_ids": [EXAMPLE_KB_ID],
        "top_k": 8,
        "debug": True,
    },
}


PATH_EXAMPLES = {
    "thread_id": EXAMPLE_THREAD_ID,
    "knowledge_base_id": EXAMPLE_KB_ID,
    "document_id": EXAMPLE_DOCUMENT_ID,
    "job_id": EXAMPLE_JOB_ID,
}


def _augment_operation(method: str, path: str, operation: dict[str, Any]) -> None:
    metadata = OPERATION_METADATA.get((method, path))
    if metadata:
        summary, description, tag = metadata
        operation["summary"] = summary
        operation["description"] = description
        operation["tags"] = [tag]

    for parameter in operation.get("parameters", []):
        name = parameter.get("name")
        if name == "x-tenant-id":
            parameter["description"] = (
                "租户 UUID；不传时使用默认测试租户。生产环境应显式传入。"
            )
            parameter["example"] = DEFAULT_TENANT_ID
        elif name in PATH_EXAMPLES:
            parameter["example"] = PATH_EXAMPLES[name]
        elif name == "path":
            parameter["example"] = "/workspace/app/output/session_<thread_id>"

    example = REQUEST_EXAMPLES.get((method, path))
    if example is not None:
        content = operation.get("requestBody", {}).get("content", {})
        media = content.get("application/json")
        if media is not None:
            media["example"] = example


def build_spec() -> dict[str, Any]:
    spec = deepcopy(app.openapi())
    spec["info"].update(
        {
            "title": "kgharness 企业级 RAG 与智能体 API",
            "version": "1.0.0",
            "description": (
                "供 Apifox 导入的接口契约。HTTP API 包含智能体任务、文件管理和企业 RAG；"
                "WebSocket 协议位于根级 x-websocket 扩展，并在配套测试说明中给出操作顺序。\n\n"
                "当前测试环境未启用业务鉴权。RAG 接口通过可选 X-Tenant-ID 进行租户隔离，"
                "请勿把该测试暴露方式直接用于生产环境。"
            ),
        }
    )
    spec["servers"] = [
        {"url": REMOTE_HTTP, "description": "云服务器测试环境"},
        {"url": "http://localhost:8000", "description": "本地 API 直连"},
    ]
    spec["tags"] = [
        {"name": "系统", "description": "服务存活与依赖健康检查"},
        {"name": "智能体任务", "description": "主智能体异步任务与取消"},
        {"name": "智能体文件", "description": "会话附件与任务产物"},
        {"name": "RAG 系统", "description": "RAG 子系统健康检查"},
        {"name": "企业 RAG", "description": "多租户知识库、摄取任务与混合检索"},
    ]

    for path, path_item in spec["paths"].items():
        for method, operation in path_item.items():
            upper_method = method.upper()
            if upper_method in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                _augment_operation(upper_method, path, operation)

    spec["x-websocket"] = {
        "name": "智能体实时事件流",
        "url": f"{REMOTE_WS}/ws/{{{{thread_id}}}}",
        "method": "WebSocket",
        "description": (
            "thread_id 必须为 UUID。连接成功后再调用 POST /api/task；客户端每 25 秒发送 ping 保活。"
        ),
        "variables": {
            "thread_id": {
                "default": EXAMPLE_THREAD_ID,
                "description": "与 HTTP 任务请求完全相同的 UUID",
            }
        },
        "clientMessages": [
            {"name": "心跳", "contentType": "text/plain", "example": "ping"}
        ],
        "serverMessages": [
            {
                "name": "心跳响应",
                "example": {"type": "pong", "message": "服务端已收到: ping"},
            },
            {
                "name": "监控事件",
                "example": {
                    "type": "monitor_event",
                    "event": "tool_start",
                    "message": "开始执行工具: search_knowledge_base",
                    "data": {"tool_name": "search_knowledge_base", "args": {}},
                    "timestamp": "2026-08-16T20:00:00+08:00",
                },
            },
        ],
        "eventTypes": [
            "session_created",
            "tool_start",
            "assistant_call",
            "task_result",
            "task_cancelled",
            "error",
        ],
    }
    return spec


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(build_spec(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(OUTPUT)


if __name__ == "__main__":
    main()
