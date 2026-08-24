# kgharness Apifox 接口测试说明

## 1. 导入文件与测试环境

在 Apifox 中选择“项目设置 → 导入数据 → OpenAPI/Swagger”，导入同目录下的
`kgharness-apifox-openapi.json`。

测试环境变量：

| 变量 | 测试值 | 说明 |
|---|---|---|
| `base_url` | `http://119.91.123.102` | HTTP API 与前端公网入口 |
| `ws_url` | `ws://119.91.123.102` | WebSocket 公网入口 |
| `tenant_id` | `00000000-0000-0000-0000-000000000001` | 默认测试租户 |
| `thread_id` | 每次测试生成一个 UUID v4 | 智能体任务与 WebSocket 必须一致 |
| `knowledge_base_id` | 创建知识库后保存 | RAG 知识库 ID |
| `document_id` | 上传文档后保存 | RAG 文档 ID |
| `job_id` | 上传文档后保存 | 异步摄取任务 ID |

当前测试环境未启用业务鉴权。企业生产环境上线前必须增加 TLS、统一身份认证、
RBAC、限流与审计，不能把仅有 `X-Tenant-ID` 的隔离方式视为身份认证。

## 2. WebSocket 测试

OpenAPI 本身不定义 WebSocket 请求，因此需要在 Apifox 中新建 WebSocket 接口：

```text
ws://119.91.123.102/ws/{{thread_id}}
```

测试步骤：

1. 将 `thread_id` 设置为合法 UUID v4，例如 `7b1f3b92-9d41-4e76-a6bf-a27877c75f09`。
2. 先连接 WebSocket，确认握手状态为已连接。
3. 发送文本消息 `ping`，预期收到：

```json
{
  "type": "pong",
  "message": "服务端已收到: ping"
}
```

4. 保持连接，再调用 `POST /api/task`，请求体中的 `thread_id` 必须与 WebSocket 相同。
5. 观察 WebSocket 推送的 `monitor_event`。可能的 `event` 包括：
   `session_created`、`tool_start`、`assistant_call`、`task_result`、
   `task_cancelled`、`error`。
6. 长连接测试时每 25 秒发送一次 `ping`。

典型事件：

```json
{
  "type": "monitor_event",
  "event": "task_result",
  "message": "任务执行完成",
  "data": {
    "result": "任务最终结果"
  },
  "timestamp": "2026-08-16T20:00:00+08:00"
}
```

如果 `thread_id` 不是 UUID，后端会拒绝握手；如果客户端显式发送 `Origin`，测试环境只允许
`http://119.91.123.102`。Apifox 桌面端通常无需手动添加 `Origin`。

## 3. 智能体任务测试链路

### 3.1 启动任务

`POST {{base_url}}/api/task`

```json
{
  "query": "分析企业级 RAG 的实施风险，并给出落地建议。",
  "thread_id": "{{thread_id}}"
}
```

预期 HTTP 状态码为 `200`，响应为：

```json
{
  "status": "started",
  "thread_id": "{{thread_id}}"
}
```

接口只表示后台任务已启动，最终结果通过 WebSocket 的 `task_result` 事件返回。

### 3.2 取消任务

`POST {{base_url}}/api/task/{{thread_id}}/cancel`

活跃任务预期返回 `cancelled` 或 `cancelling`；任务不存在或已经结束时返回 `404`。

### 3.3 上传任务附件

`POST {{base_url}}/api/upload`，请求类型为 `multipart/form-data`：

| 字段 | 类型 | 值 |
|---|---|---|
| `thread_id` | Text | `{{thread_id}}` |
| `files` | File | 可重复添加多个文件 |

单个文件最大 100 MB。预期返回 `status=uploaded` 和服务端保存的文件名列表。

### 3.4 查询和下载任务产物

从 WebSocket 的 `session_created.data.path` 取得目录后调用：

```text
GET {{base_url}}/api/files?path=<URL 编码后的 session path>
```

再把响应中的 `files[].path` 传给：

```text
GET {{base_url}}/api/download?path=<URL 编码后的 file path>
```

`path` 只能位于服务端 `output` 目录内，目录之外的路径会被拒绝。

## 4. 企业 RAG 测试链路

下列接口建议统一传请求头：

```text
X-Tenant-ID: {{tenant_id}}
```

不传时服务端使用默认测试租户。

### 4.1 创建知识库

`POST {{base_url}}/api/v1/knowledge-bases`

```json
{
  "name": "Apifox 测试知识库",
  "description": "接口自动化测试"
}
```

预期状态码 `201`。保存响应中的 `id` 为 `knowledge_base_id`。同一租户重复名称返回 `409`。

### 4.2 上传知识库文档

`POST {{base_url}}/api/v1/knowledge-bases/{{knowledge_base_id}}/documents`

请求类型为 `multipart/form-data`，字段 `file` 选择文件。支持：
`.pdf`、`.docx`、`.xlsx`、`.csv`、`.md`、`.txt`、`.html`、`.htm`。

预期状态码 `202`。保存：

- `document.id` → `document_id`
- `job.id` → `job_id`
- `created` → 是否新建；重复文件可能复用已有记录

### 4.3 查询知识库文档

```text
GET {{base_url}}/api/v1/knowledge-bases/{{knowledge_base_id}}/documents?limit=100&offset=0
```

返回当前知识库的文档及每个文档的 `latest_job`。前端知识库管理页使用该接口在刷新后恢复任务状态。

### 4.4 轮询摄取任务

每 2 秒调用：

```text
GET {{base_url}}/api/v1/ingestion-jobs/{{job_id}}
```

当 `status=completed` 且 `progress=100` 时进入检索测试。若 `status=failed`，先记录
`error_code` 和 `error_message`，修复依赖后调用：

```text
POST {{base_url}}/api/v1/ingestion-jobs/{{job_id}}/retry
```

只有失败任务允许重试，其他状态返回 `409`。

### 4.5 混合检索

`POST {{base_url}}/api/v1/retrieval/search`

```json
{
  "query": "文档中最重要的要求是什么？",
  "knowledge_base_ids": ["{{knowledge_base_id}}"],
  "top_k": 8,
  "debug": true
}
```

成功响应应包含：

- `query_id`：本次查询 UUID；
- `evidence[]`：引用编号、文档、知识库、正文、页码与各阶段分数；
- `insufficient_evidence`：证据是否不足；
- `timings`：Embedding、Dense、Sparse、RRF、Rerank 与总耗时；
- `warnings`：例如 Rerank 降级信息；
- `debug`：仅在请求 `debug=true` 时返回调试信息。

`knowledge_base_ids=[]` 表示在当前租户全部知识库范围内检索。

## 5. 建议断言

| 接口 | 关键断言 |
|---|---|
| `/health` | HTTP `200`，`status=ok`，`database=ok` |
| `/api/v1/rag/health` | HTTP `200`，`status=ok` |
| 创建知识库 | HTTP `201`，`id` 是 UUID，`status=active` |
| 上传文档 | HTTP `202`，同时返回 `document` 与 `job` |
| 摄取任务 | 最终 `status=completed`、`progress=100` |
| 混合检索 | HTTP `200`，`query_id` 是 UUID，`evidence` 是数组 |
| WebSocket 心跳 | 发送 `ping` 后收到 `type=pong` |
| 智能体任务 | 先返回 `started`，最终收到 `event=task_result` 或 `event=error` |

建议额外覆盖非法 UUID、无效租户、重复知识库名称、不支持文件格式、超过 100 MB、
不存在的任务和 output 目录外路径等异常场景。
