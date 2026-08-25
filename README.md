## 📖 项目介绍
*目前正在开发中*

在真实研究场景里，用户的问题经常不是一句普通问答可以解决的。

比如：

```text
结合公开资料、数据库信息和我上传的文档，整理一份机器人行业研究报告，并生成 PDF。
```

这个任务背后可能包含多类动作：

- 判断需要公开资料、内部数据、私有知识库还是本次上传文件；
- 去互联网搜索最新新闻、政策、产品或行业资料；
- 到 PostgreSQL 查询企业结构化业务数据；
- 到内置 LangGraph RAG 子图查询企业知识库；
- 读取用户上传的 PDF、Word、Excel、Markdown 或文本文件；
- 汇总多来源信息，判断资料是否足够；
- 生成 Markdown 报告，并在需要时转换成 PDF；
- 把执行过程、最终结果和生成文件实时展示给前端。
面向个人的多智能体协作的harness agent项目，可以进行深度搜索，文档生成，文件系统管理，可以分工、会查资料、会生成交付物的研究助手。用户只需要提出任务，系统会在后端组织一条可观察的多智能体执行链路。

```text
用户任务
  -> FastAPI 接口接收请求
  -> run_deep_agent 创建会话目录并写入上下文
  -> 主智能体分析任务
  -> 分派给网络搜索助手 / 数据库查询助手 / 企业 RAG 助手
  -> 主智能体汇总多来源信息
  -> 调用文件工具生成 Markdown / PDF
  -> Worker 将事件写入 Redis Streams，API 通过 SSE 推送
  -> 前端展示事件、答案和文件列表
```

## ✨ 项目细节

- **一主三从的多智能体架构**
  - 主智能体负责理解任务、规划步骤、调度助手和最终汇总。
  - 网络搜索助手、数据库查询助手、企业 RAG 助手分别处理不同信息来源。
- **多来源检索，而不是模型裸答**
  - `Tavily` 负责互联网公开资料检索。
  - `PostgreSQL` 负责结构化业务数据、Run 状态和 LangGraph Checkpoint。
  - `pgvector + 全文检索 + RRF + rerank` 负责企业知识库混合检索。
  - 上传附件由主智能体通过文件工具读取。
- **从检索到交付的完整可运行链路**
  - 不停留在 Prompt 设计，而是会真实调用工具、读取数据、生成 Markdown，并在需要时转换成 PDF。
- **长任务执行过程可观察**
  - 可见文本增量、图节点、工具与子智能体调用、取消和终态通过可重放 SSE 推送；不暴露隐藏思维链。
- **会话级上下文隔离**
  - 通过 `thread_id` 和 `session_dir` 区分不同任务，`ContextVar` 让深层工具也能拿到当前会话身份和文件目录。
- **工程化前后端结构清晰**
  - 基于 `FastAPI + Redis Streams + Agent Worker + React` 组织持久化 Run、异步执行和实时事件。



## 🏗️ 系统架构

当前企业化执行面已经拆分为 FastAPI、Redis Streams Agent Queue、独立 Agent Worker、
PostgreSQL/pgvector 和可重连 SSE。完整的组件边界、Run 生命周期与后续安全阶段见
[`docs/enterprise-architecture.md`](docs/enterprise-architecture.md)。

项目采用 DeepAgents 中典型的 Orchestrator-Workers 模式：主智能体作为调度中心，三个专家助手负责信息获取，文件工具由主智能体直接掌握。

项目围绕两条主线展开：

| 主线             | 做什么                                                       | 涉及模块                                                                  |
| ---------------- | ------------------------------------------------------------ | ------------------------------------------------------------------------- |
| 多智能体深度研搜 | 基于用户任务完成规划、分派、检索、读取附件、汇总和生成交付物 | `DeepAgents` / `LangChain` / `LangGraph` / `Tavily` / `PostgreSQL` / `pgvector` |
| 前后端实时闭环   | 持久化并调度 Run、重放执行过程、展示增量结果和下载生成文件 | `FastAPI` / `Redis Streams` / `SSE` / `React` / `Vite` |

### 智能体与工具

| 归属           | 能力                                     | 工具                                                          |
| -------------- | ---------------------------------------- | ------------------------------------------------------------- |
| 主智能体       | 任务规划、助手调度、结果汇总、文件交付   | `read_file_content`、`generate_markdown`、`convert_md_to_pdf` |
| 网络搜索助手   | 查询互联网公开信息、新闻、政策和网页资料 | `internet_search`                                             |
| 数据库查询助手 | 发现表名、预览表结构和样例数据、执行 SQL | `list_sql_tables`、`get_table_data`、`execute_sql_query`      |
| 企业 RAG 助手 | 对私有文档执行稠密/稀疏混合检索、融合、重排和证据引用 | 内置 `rag_graph` |



## 🛠️ 项目技术栈

| 模块           | 技术                                             | 作用                                                                          |
| -------------- | ------------------------------------------------ | ----------------------------------------------------------------------------- |
| 智能体框架     | `DeepAgents`                                     | 创建主智能体和子智能体，承接长任务、多工具、多助手调度                        |
| 图与检查点     | `LangGraph` / `AsyncPostgresSaver`               | 提供多智能体运行时和 PostgreSQL 持久化会话检查点                              |
| 模型与工具抽象 | `LangChain` / `langchain-core`                   | 封装 OpenAI 兼容模型、工具声明和 Agent 调用结构                               |
| 大模型接入     | OpenAI 兼容接口                                  | 通过 `.env` 中的 `OPENAI_BASE_URL`、`OPENAI_API_KEY`、`LLM_QWEN_MAX` 接入模型 |
| 网络搜索       | `Tavily`                                         | 为网络搜索助手提供公开资料检索                                                |
| 结构化数据     | `PostgreSQL` / `psycopg`                         | 为数据库助手提供只读业务查询                                                  |
| 私有知识库     | `pgvector` / PostgreSQL FTS / RRF / reranker     | 多租户文档入库、混合召回、重排与证据引用                                     |
| 文件处理       | `pypdf` / `python-docx` / `pandas` / `ReportLab` | 读取上传附件，生成 Markdown，转换 PDF                                         |
| 后端接口       | `FastAPI` / `Uvicorn`                            | 提供 Run、取消、SSE、RAG、上传与产物接口                                      |
| 执行传输       | `Redis Streams`                                  | Run 队列、消费者组、租约恢复、取消信号和短期事件流                            |
| 实时通信       | `SSE`                                            | 按事件 ID 重连回放可见文本增量和可审计执行轨迹                                |
| 前端           | `React` / `Vite` / `Ant Design` / `Tailwind CSS` | 提供对话式研搜界面、事件流、附件上传和文件下载                                |
| 依赖管理       | `uv` / `pnpm`                                    | 管理 Python 后端和前端依赖                                                    |

## 📁 项目结构

```text
deepsearch-agents/
├── app/
│   ├── agent/
│   │   ├── subagents/              # 网络搜索、数据库查询、企业 RAG 三个子智能体
│   │   ├── worker.py               # Redis Streams Agent Worker
│   │   ├── llm.py                  # OpenAI 兼容模型初始化
│   │   ├── main_agent.py           # 主智能体组装与 run_deep_agent 执行入口
│   │   └── prompts.py              # 读取 app/prompt/prompts.yml
│   ├── api/
│   │   ├── context.py              # ContextVar 保存 thread_id 和 session_dir
│   │   ├── monitor.py              # 工具调用、助手调用、结果和异常事件推送
│   │   └── server.py               # FastAPI Run、SSE、上传、产物及兼容接口
│   ├── core/                        # 执行配置与 Redis transport
│   ├── services/                    # Run 执行、落库与终态收敛
│   ├── chat/                        # 会话与 agent_runs PostgreSQL 仓储
│   ├── rag/                         # LangGraph RAG、混合检索、入库与 Worker
│   ├── prompt/
│   │   └── prompts.yml             # 主智能体和子智能体提示词配置
│   ├── tools/                      # Tavily、PostgreSQL、文件读取、Markdown、PDF 工具
│   ├── utils/                      # 路径解析、Markdown/PDF 底层转换等普通 Python 工具
│   ├── output/                     # 运行时生成：每个会话的 Markdown、PDF 等产物
│   └── updated/                    # 运行时生成：用户上传文件的会话暂存目录
├── docker/
│   └── compose.yaml                # API、Agent/RAG Worker、PostgreSQL、前端
├── docs/enterprise-architecture.md # 企业执行面设计与演进边界
├── examples/                       # DeepAgents 章节示例脚本
├── frontend/                       # React + Vite 前端项目
├── tests/                          # 测试目录
├── .env.example                    # 环境变量示例
├── pyproject.toml                  # Python 项目依赖声明
└── uv.lock                         # uv 锁定文件
```

## 🚀 快速开始

### 1. 准备环境

- Python `3.12`
- `uv`
- Docker 与 Docker Compose
- Node.js 与 `pnpm`
- 可用的大模型 API Key
- Tavily API Key
- 一个启用 pgvector 的 PostgreSQL 17 实例
- 一个启用 ACL/密码、且仅受信网络可访问的 Redis 实例


### 3. 安装后端依赖

```bash
uv sync
```

### 4. 配置环境变量

```bash
cp .env.example .env
```

按本机实际服务和密钥修改 `.env`：

```bash
# LLM 配置
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_API_KEY=你的大模型_API_KEY
LLM_QWEN_MAX=qwen-max

# Tavily 配置
TAVILY_API_KEY=你的_TAVILY_API_KEY

# PostgreSQL / Redis
RAG_DATABASE_URL=postgresql://kgharness:password@postgres:5432/kgharness
BUSINESS_DATABASE_URL=postgresql://kgharness:password@postgres:5432/kgharness
RUN_EXECUTION_MODE=distributed
REDIS_URL=redis://:password@119.91.123.102:6379/0
```

### 5. 准备 PostgreSQL 与 Redis

PostgreSQL 保存 Run、会话、Checkpoint、RAG 元数据和向量；Redis 仅保存队列、租约、取消信号和短期事件。远程 Redis 必须先启用 ACL/密码并限制安全组，禁止匿名暴露 6379。

### 6. 一键启动分布式栈

```bash
docker compose --env-file .env -f docker/compose.yaml up -d --build
```

这会启动 PostgreSQL、FastAPI、Agent Worker、RAG 入库 Worker 和前端；Redis 使用 `.env` 中配置的远程实例。

生产环境建议至少运行两个 Agent Worker；它们可以安全竞争同一 Redis
Consumer Group，PostgreSQL 中的 Run 租约负责防止重复执行：

```bash
docker compose --env-file .env -f docker/compose.yaml up -d --build --scale agent-worker=2
```

完整的高可用拓扑、重试语义、健康检查和告警阈值见
[`docs/enterprise-runtime.md`](docs/enterprise-runtime.md)。

### 7. 本地分别启动（开发模式）


```bash
RUN_EXECUTION_MODE=distributed uv run uvicorn app.api.server:app --host 0.0.0.0 --port 8000 --reload
RUN_EXECUTION_MODE=distributed uv run python -m app.agent.worker
uv run python -m app.rag.worker
```

后端默认接口：

| 接口                                | 说明                                   |
| ----------------------------------- | -------------------------------------- |
| `POST /api/task`                    | 启动一次 DeepAgents 后台任务           |
| `POST /api/task/{thread_id}/cancel` | 取消指定会话任务                       |
| `GET /api/runs/{run_id}`            | 查询持久化 Run 状态                    |
| `GET /api/runs/{run_id}/events`     | 可重连、可回放的 SSE 执行事件流        |
| `POST /api/runs/{run_id}/cancel`    | 按独立 Run ID 发出分布式取消信号       |
| `POST /api/upload`                  | 上传一个或多个文件到当前会话           |
| `GET /api/files`                    | 列出当前会话输出目录中的生成文件       |
| `GET /api/download`                 | 下载输出目录中的文件                   |
| `WebSocket /ws/{thread_id}`         | 仅供 `local` 模式兼容的实时事件通道     |
| `GET /health/live`                  | 进程存活探针，不检查外部依赖             |
| `GET /health/ready`                 | PostgreSQL、Redis、Worker 与队列就绪探针  |

### 8. 启动前端开发服务器

```bash
cd frontend
pnpm install
pnpm dev


```

前端默认连接：

```text
API: http://localhost:8000
SSE: http://localhost:8000/api/runs/{run_id}/events
```

如需修改，可以在 `frontend/.env.local` 中配置：

```bash
VITE_API_BASE_URL=http://localhost:8000
```



### 9. 试几个任务

```text
从数据库中查询心血管药品的库存情况，并生成 Markdown 报告。
```

```text
搜索 2026 年 AI 在电商行业的应用趋势，并结合知识库资料生成一份 PDF。
```

```text
请先读取我上传的行业报告，再结合公开资料整理一份研究摘要。
```
