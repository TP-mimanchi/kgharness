# KG Harness 企业级运行方案

## 目标与边界

本方案面向长时间运行、允许滚动升级和多 Worker 横向扩容的生产环境。业务状态以
PostgreSQL 为准；Redis 负责可恢复队列、短期运行状态、取消信号和 SSE 事件回放。
API 不执行分布式 Agent，因此可以独立扩容。

推荐生产拓扑：

```text
Load Balancer
  └─ API × 2+
       ├─ PostgreSQL HA / 托管版
       └─ Redis HA / 托管版（AOF）

Redis Consumer Group
  └─ Agent Worker × 2+
       ├─ PostgreSQL checkpoint pool
       ├─ 共享上传/输出存储
       └─ LLM / Search / Embedding providers
```

Docker Compose 是单机生产参考。跨节点部署时，`app/updated` 与 `app/output` 必须
使用 RWX 存储（NFS/CephFS）或改造成对象存储；不能使用各节点自己的临时磁盘。

## 会话与并发语义

- 同一会话一次只允许一个活动 Run，保证 LangGraph checkpoint 和消息顺序一致。
- 不同会话由 Redis Consumer Group 分发，可同时占用不同 Worker 执行槽。
- 前端活动会话 ID 保存在 `sessionStorage`，不同浏览器标签页默认创建不同会话；
  用户仍可从历史列表显式打开同一会话。
- 实际并发容量为 `Worker 副本数 × AGENT_WORKER_CONCURRENCY`，Redis 只提供可靠
  排队和分发，不替代 Worker 执行容量。

## 已实现的可靠性不变量

1. **连接不会永久中毒**：Chat、RAG 和 LangGraph checkpoint 全部使用
   `AsyncConnectionPool`。每次借出连接执行 `SELECT 1`，坏连接会被丢弃并补充；连接
   默认 30 分钟轮换、空闲 5 分钟回收。
2. **幂等输入**：每次 Run 的 LangGraph 用户消息 ID 固定为
   `run:{run_id}:user`。基础设施重试不会重复追加同一条用户消息。
3. **原子终态**：`agent_runs` 的终态和该 Run 唯一的 assistant 消息在一个
   PostgreSQL 事务中提交。进程在任意一步退出都不会产生“已完成但没答案”或双答案。
4. **双租约恢复**：执行中同时续租 Redis Pending Entry 与 PostgreSQL
   `last_heartbeat_at`。只有两边均超过租期，新 Worker 才会接管崩溃实例的 Run。
5. **有限重试**：仅连接断开、超时、连接池超时等瞬时基础设施错误自动重试；认证、
   参数、业务工具错误立即失败。默认总执行次数为 3，退避为 1s、2s。
6. **死信保留**：重试耗尽的 Run 写入 Redis `runs:dead-letter` Stream，同时在
   PostgreSQL 原子落为 `failed`，会话锁随即释放。
7. **Worker 准入**：Worker 每 10 秒登记进程心跳。没有健康 Worker 时，API 返回
   503 且不会创建用户消息或 queued Run，避免产生永远排队的会话。
8. **可回放事件**：同一 Run ID 在重试期间保持不变，SSE 客户端继续复用原事件流，
   `event_id` 和 `call_id` 防止 UI 重复累计。

## 配置基线

| 配置 | 建议值 | 说明 |
|---|---:|---|
| `AGENT_WORKER_CONCURRENCY` | 1–2 | 单进程并发；CPU/内存紧张时设为 1 |
| `AGENT_WORKER_CLAIM_IDLE_MS` | 120000 | 崩溃任务接管阈值，必须远大于心跳间隔 |
| `AGENT_WORKER_HEARTBEAT_SECONDS` | 10 | Run 与进程心跳周期 |
| `AGENT_WORKER_REGISTRY_TTL_SECONDS` | 45 | Worker 就绪租约，必须大于两倍心跳 |
| `AGENT_RUN_MAX_ATTEMPTS` | 3 | 包含首次执行的总次数 |
| `POSTGRES_POOL_MAX_LIFETIME_SECONDS` | 1800 | 小于 NAT/LB 常见空闲回收时间 |
| `CHECKPOINT_POOL_MAX_SIZE` | 4 | 通常为 Worker 并发的 2 倍 |

数据库连接上限至少按下面公式预留，并保留 30% 管理余量：

```text
API副本 × (RAG池8 + Chat池4)
+ Worker副本 × (RAG池8 + Chat池4 + Checkpoint池4)
```

规模更大时应下调每进程池大小，或在 PostgreSQL 前部署 PgBouncer（transaction
pooling）。

## 健康检查

- `/health/live`：只确认 API 事件循环存活，适合作为 liveness probe。
- `/health/ready`：检查 RAG/Chat PostgreSQL、Redis、健康 Worker 数和队列统计。
  任一关键依赖不可用时返回 503。
- Worker 容器执行 `python -m app.agent.worker_health`，检查由事件循环更新的本地
  heartbeat 文件。事件循环卡死时容器会变为 unhealthy。

建议告警：

- `agent_workers == 0` 持续 30 秒：P1。
- `queue.lag > 20` 持续 5 分钟：P2；结合吞吐设置动态阈值。
- `queue.pending > 0` 且最老 Pending 超过 `2 × CLAIM_IDLE`：P1。
- dead-letter 5 分钟增量大于 0：P1。
- PostgreSQL pool timeout、Redis timeout 或 Run retry rate > 2%：P2。

## 发布与故障演练

发布顺序：先迁移/启动新 Worker，再滚动 API，最后停止旧 Worker。Worker 收到
SIGTERM 后停止领取任务，最多等待 `AGENT_WORKER_SHUTDOWN_GRACE_SECONDS`，超时才
取消剩余协程。生产环境保持至少一个健康 Worker，滚动策略使用
`maxUnavailable=0`。

上线前至少演练：

1. 执行中关闭一个 Worker，确认 120 秒后其他 Worker 接管且只产生一条答案。
2. 临时阻断 PostgreSQL，恢复后确认连接池补充连接、Run 自动重试。
3. 临时阻断 Redis，确认 API readiness 变为 503 且不创建孤儿 Run。
4. 让三次瞬时重试全部失败，确认 dead-letter、会话失败消息和锁释放一致。
5. SSE 断线重连，确认 Token、工具和子智能体次数不重复。

密钥必须由 Secret Manager/Kubernetes Secret 注入；日志和健康接口不得输出 DSN、
API Key、Redis 密码或用户查询全文。
