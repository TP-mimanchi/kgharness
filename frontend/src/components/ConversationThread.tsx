import {
  BranchesOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  DownloadOutlined,
  FileMarkdownOutlined,
  FilePdfOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  RobotOutlined,
  StopOutlined,
  ToolOutlined,
} from "@ant-design/icons";
import { Button, Tooltip } from "antd";
import { memo, useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { getDownloadUrl } from "../lib/api";
import { countEvents, uniqueEvents } from "../lib/telemetry";
import { MarkdownRenderer } from "./MarkdownRenderer";
import type { MonitorMessage, OutputFile } from "../types";

type AgentPhase = "listening" | "planning" | "executing" | "complete";

const AUDIT_EVENT_NAMES = new Set([
  "run_queued",
  "run_started",
  "run_retrying",
  "activity",
  "session_created",
  "model_call",
  "node_completed",
  "assistant_call",
  "tool_start",
  "retryable_error",
  "cancel_requested",
  "task_cancelled",
  "run_cancelled",
  "task_result",
  "run_completed",
  "error",
  "run_failed",
]);

interface AuditEventPresentation {
  label: string;
  message: string;
  context?: string;
}

export interface ChatTurn {
  id: string;
  threadId: string;
  content: string;
  events: MonitorMessage[];
  files: OutputFile[];
  isRunning: boolean;
  result: string;
  timestamp: string;
}

interface ConversationThreadProps {
  onUseExample: (prompt: string) => void;
  turns: ChatTurn[];
  transport: "websocket" | "sse";
}

const TASK_EXAMPLES = [
  {
    tool: "网络搜索工具",
    title: "联网趋势研判",
    prompt:
      "请使用网络搜索工具，检索 2026 年跨境电商 AI 客服趋势，列出 5 条关键变化，并附上来源链接。",
    icon: <CloudServerOutlined aria-hidden />,
  },
  {
    tool: "数据库查询工具",
    title: "药品库存排查",
    prompt:
      "请使用数据库查询工具，查询库存大于 100 的药品，按库存量升序列出药品名称、批次号、仓库位置和过期日期。",
    icon: <DatabaseOutlined aria-hidden />,
  },
  {
    tool: "企业知识库",
    title: "内部文档问答",
    prompt:
      "请使用企业知识库助手，查询公司内部白皮书中关于品类策略的内容，并整理成三条可执行建议。",
    icon: <FileSearchOutlined aria-hidden />,
  },
  {
    tool: "文件读取工具",
    title: "上传文件分析",
    prompt:
      "请使用文件读取工具，读取我上传的文件，提炼核心观点、风险点和待补充信息，并给出下一步分析计划。",
    icon: <FileTextOutlined aria-hidden />,
  },
  {
    tool: "Markdown/PDF 工具",
    title: "生成交付报告",
    prompt:
      "请使用 Markdown 文档生成工具和 Markdown 转 PDF 工具，基于本次调研结果生成一份 Markdown 报告，并转换成 PDF 保存到当前工作目录。",
    icon: <FileMarkdownOutlined aria-hidden />,
  },
];

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "--:--";
  }
  return date.toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatBytes(value: number): string {
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function parseTime(value: string): number | null {
  const time = new Date(value).getTime();
  return Number.isNaN(time) ? null : time;
}

function formatDuration(value: number): string {
  const totalSeconds = Math.max(0, Math.floor(value / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const paddedMinutes = String(minutes).padStart(2, "0");
  const paddedSeconds = String(seconds).padStart(2, "0");

  if (hours > 0) {
    return `${hours}:${paddedMinutes}:${paddedSeconds}`;
  }
  return `${paddedMinutes}:${paddedSeconds}`;
}

function dataString(event: MonitorMessage, key: string): string | null {
  const value = event.data[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function nestedDataString(
  event: MonitorMessage,
  parentKey: string,
  key: string,
): string | null {
  const parent = event.data[parentKey];
  if (!parent || typeof parent !== "object") return null;
  const value = (parent as Record<string, unknown>)[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function auditEvents(events: MonitorMessage[]): MonitorMessage[] {
  return uniqueEvents(events).filter((event) => AUDIT_EVENT_NAMES.has(event.event));
}

function nodeLabel(node: string | null): string {
  if (!node) return "执行节点";
  if (node === "model") return "Agent 推理";
  if (node === "tools") return "工具执行";
  if (node.includes("before_agent")) return "上下文准备";
  if (node.includes("after_model")) return "输出检查";
  return node.replaceAll("_", " ");
}

function presentAuditEvent(event: MonitorMessage): AuditEventPresentation {
  if (event.event === "run_queued") {
    return { label: "任务排队", message: "任务已进入队列，等待 Agent Worker 领取" };
  }
  if (event.event === "run_started") {
    return { label: "Agent 启动", message: "Agent Worker 已领取任务，正在初始化执行上下文" };
  }
  if (event.event === "run_retrying") {
    const attempt = event.data.attempt;
    const suffix = typeof attempt === "number" ? `（第 ${attempt + 1} 次）` : "";
    return { label: "自动重试", message: `基础设施暂时不可用，Agent 正在重新调度${suffix}` };
  }
  if (event.event === "activity") {
    return { label: "Agent 动作", message: event.message };
  }
  if (event.event === "session_created") {
    return { label: "工作区", message: "会话工作区已准备完成，Agent 正在分析任务" };
  }
  if (event.event === "model_call") {
    return { label: "Agent 推理", message: "Agent 已完成本轮推理，正在确定下一步动作" };
  }
  if (event.event === "node_completed") {
    return {
      label: "执行节点",
      message: `${nodeLabel(dataString(event, "node"))}已完成，正在进入下一阶段`,
    };
  }
  if (event.event === "assistant_call") {
    const assistantName = dataString(event, "assistant_name") || "子智能体";
    const description = nestedDataString(event, "args", "description");
    return {
      label: "子智能体",
      message: `正在调度子智能体 · ${assistantName}`,
      context: description || undefined,
    };
  }
  if (event.event === "tool_start") {
    const toolName = dataString(event, "tool_name") || "业务工具";
    return { label: "工具调用", message: `正在执行工具 · ${toolName}` };
  }
  if (event.event === "retryable_error") {
    return { label: "连接恢复", message: "基础设施连接暂时不可用，Agent 正在准备自动重试" };
  }
  if (event.event === "cancel_requested") {
    return { label: "取消任务", message: "已收到停止请求，正在安全结束当前执行" };
  }
  if (event.event === "task_cancelled" || event.event === "run_cancelled") {
    return { label: "任务已停止", message: "Agent 已停止执行并释放运行资源" };
  }
  if (event.event === "task_result") {
    return { label: "生成结果", message: "Agent 已形成最终答复，正在保存会话记录" };
  }
  if (event.event === "run_completed") {
    return { label: "执行完成", message: "任务结果已保存，可继续在当前会话中提问" };
  }
  if (event.event === "run_failed") {
    return { label: "执行失败", message: "任务重试次数已耗尽，执行已安全结束" };
  }
  if (event.event === "error") {
    return { label: "执行异常", message: event.message };
  }
  return { label: "Agent 事件", message: event.message };
}

function getLastEventTime(
  events: MonitorMessage[],
  eventName?: string,
): number | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (!eventName || event.event === eventName) {
      return parseTime(event.timestamp);
    }
  }
  return null;
}

function getThinkingDuration(
  events: MonitorMessage[],
  fallbackStart: string,
  isRunning: boolean,
  now: number,
): string {
  const startedAt =
    (events[0] ? parseTime(events[0].timestamp) : null) ??
    parseTime(fallbackStart) ??
    now;
  const finishedAt =
    getLastEventTime(events, "task_result") ??
    (!isRunning ? getLastEventTime(events) : null) ??
    now;
  return formatDuration(finishedAt - startedAt);
}

function EventIcon({ event }: { event: string }) {
  if (event === "model_call") {
    return <RobotOutlined aria-hidden />;
  }
  if (event === "activity") {
    return <ClockCircleOutlined aria-hidden />;
  }
  if (event === "run_queued" || event === "run_started" || event === "run_retrying") {
    return <ClockCircleOutlined aria-hidden />;
  }
  if (event === "node_completed") {
    return <BranchesOutlined aria-hidden />;
  }
  if (event === "assistant_call") {
    return <BranchesOutlined aria-hidden />;
  }
  if (event === "tool_start") {
    return <ToolOutlined aria-hidden />;
  }
  if (event === "session_created") {
    return <FileSearchOutlined aria-hidden />;
  }
  if (event === "task_result" || event === "run_completed") {
    return <CheckCircleOutlined aria-hidden />;
  }
  if (event === "task_cancelled" || event === "run_cancelled") {
    return <StopOutlined aria-hidden />;
  }
  if (event === "cancel_requested") {
    return <StopOutlined aria-hidden />;
  }
  if (event === "error" || event === "run_failed" || event === "retryable_error") {
    return <CloseCircleOutlined aria-hidden />;
  }
  return <ClockCircleOutlined aria-hidden />;
}

function FileIcon({ name }: { name: string }) {
  if (name.endsWith(".pdf")) {
    return <FilePdfOutlined aria-hidden />;
  }
  if (name.endsWith(".md")) {
    return <FileMarkdownOutlined aria-hidden />;
  }
  return <FileTextOutlined aria-hidden />;
}

function describeCurrentActivity(events: MonitorMessage[], result: string): string {
  const visibleEvents = auditEvents(events);
  const latest = visibleEvents[visibleEvents.length - 1];
  if (!latest) return result ? "正在流式生成与整理最终回答" : "正在接收任务并准备执行环境";
  return presentAuditEvent(latest).message;
}

function ThinkingTimeline({
  events,
  isRunning,
  result,
}: {
  events: MonitorMessage[];
  isRunning: boolean;
  result: string;
}) {
  const visibleEvents = auditEvents(events);
  const timelineRef = useRef<HTMLOListElement | null>(null);
  const timelineFollowRef = useRef(true);

  useEffect(() => {
    const timelineNode = timelineRef.current;
    if (!timelineNode) {
      return;
    }

    if (timelineFollowRef.current) {
      window.requestAnimationFrame(() => {
        timelineNode.scrollTop = timelineNode.scrollHeight;
      });
    }
  }, [visibleEvents.length]);

  if (visibleEvents.length === 0) {
    return (
      <>
        {isRunning ? (
          <div className="current-activity" aria-live="polite">
            <span className="current-activity-pulse" aria-hidden />
            <div><small>AGENT 当前动作</small><strong>{describeCurrentActivity(events, result)}</strong></div>
          </div>
        ) : null}
        <div className="thinking-empty">
          <ClockCircleOutlined aria-hidden />
          等待后端推送执行事件
        </div>
      </>
    );
  }

  return (
    <>
      {isRunning ? (
        <div className="current-activity" aria-live="polite">
          <span className="current-activity-pulse" aria-hidden />
          <div><small>AGENT 当前动作</small><strong>{describeCurrentActivity(events, result)}</strong></div>
        </div>
      ) : null}
      <ol
        className="thinking-timeline"
        onScroll={(event) => {
          const node = event.currentTarget;
          timelineFollowRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 40;
        }}
        ref={timelineRef}
      >
        {visibleEvents.map((event, index) => {
          const presentation = presentAuditEvent(event);
          return (
            <li
              className={`thinking-event thinking-event--${event.event}`}
              key={`${event.timestamp}-${index}`}
            >
              <span className="thinking-event-icon">
                <EventIcon event={event.event} />
              </span>
              <div>
                <div className="thinking-event-meta">
                  <span>{presentation.label}</span>
                  <time dateTime={event.timestamp}>
                    {formatTime(event.timestamp)}
                  </time>
                </div>
                <p>{presentation.message}</p>
                {presentation.context ? (
                  <small className="thinking-event-context">{presentation.context}</small>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
    </>
  );
}

function ArtifactShelf({ files }: { files: OutputFile[] }) {
  if (files.length === 0) {
    return (
      <div className="artifact-empty">
        <FileSearchOutlined aria-hidden />
        暂无输出文件
      </div>
    );
  }

  return (
    <div className="artifact-shelf">
      {files.map((file) => (
        <div className="artifact-card" key={file.path}>
          <span className="artifact-icon">
            <FileIcon name={file.name} />
          </span>
          <div className="artifact-copy">
            <strong title={file.name}>{file.name}</strong>
            <span>{formatBytes(file.size)}</span>
          </div>
          <Tooltip title="下载">
            <Button
              aria-label={`下载 ${file.name}`}
              className="artifact-download"
              href={getDownloadUrl(file.path)}
              icon={<DownloadOutlined />}
              shape="circle"
            />
          </Tooltip>
        </div>
      ))}
    </div>
  );
}

const PHASE_LABELS: Record<AgentPhase, string> = {
  listening: "正在聆听",
  planning: "正在规划",
  executing: "正在执行",
  complete: "已完成",
};

function ThinkingLoader({ durationLabel, phase }: { durationLabel: string; phase: AgentPhase }) {
  return (
    <div
      className={`thinking-loader thinking-loader--${phase}`}
      aria-live="polite"
      aria-label={PHASE_LABELS[phase]}
    >
      <div className="loader-status">
        <span className="loader-pulse" aria-hidden />
        <strong>{PHASE_LABELS[phase]}</strong>
        <span className="loader-duration">已运行 {durationLabel}</span>
        <span className="loader-dots" aria-hidden>
          <i />
          <i />
          <i />
        </span>
      </div>
      <div className="loader-track" aria-hidden />
      <ul className="loader-steps" aria-hidden>
        <li>理解问题</li>
        <li>调度工具</li>
        <li>汇总答案</li>
      </ul>
    </div>
  );
}

function TurnTelemetry({ events }: { events: MonitorMessage[] }) {
  const modelCalls = countEvents(events, "model_usage") || countEvents(events, "model_call");
  const toolCalls = countEvents(events, "tool_start");
  const assistantCalls = countEvents(events, "assistant_call");

  return (
    <header className="turn-telemetry" aria-label="本轮调用统计">
      <span className="turn-telemetry-label">THIS TURN</span>
      <dl>
        <div><dt>模型</dt><dd>{modelCalls}</dd></div>
        <div><dt>工具</dt><dd>{toolCalls}</dd></div>
        <div><dt>子智能体</dt><dd>{assistantCalls}</dd></div>
      </dl>
    </header>
  );
}

function AssistantMessage({
  events,
  files,
  isRunning,
  result,
  timestamp,
  transport,
}: Pick<ChatTurn, "events" | "files" | "isRunning" | "result" | "timestamp"> & {
  transport: "websocket" | "sse";
}) {
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    if (!isRunning) {
      return;
    }

    const timer = window.setInterval(() => {
      setNow(Date.now());
    }, 1000);

    return () => window.clearInterval(timer);
  }, [isRunning]);

  const durationLabel = getThinkingDuration(events, timestamp, isRunning, now);
  const visibleAuditEvents = auditEvents(events);
  const isCancelled = events.some(
    (event) => event.event === "task_cancelled" || event.event === "run_cancelled"
  );
  let agentPhase: AgentPhase = "listening";
  if (!isRunning && result) {
    agentPhase = "complete";
  } else if (events.some((event) => event.event === "tool_start")) {
    agentPhase = "executing";
  } else if (events.length > 0) {
    agentPhase = "planning";
  }
  const syncLabel = isRunning
    ? `生成中 · 运行 ${durationLabel}`
    : `${isCancelled ? "已取消" : "已同步"} · 用时 ${durationLabel}`;

  return (
    <article className="chat-message chat-message--assistant">
      <div className="message-avatar">TP</div>
      <div className="message-bubble">
        <div className="message-meta">
          <span>TP · KG Agent</span>
          <time>{syncLabel}</time>
        </div>

        <details
          className="thinking-block"
          open={isRunning || visibleAuditEvents.length > 0}
        >
          <summary>
            <span>
              <BranchesOutlined aria-hidden />
              可审计执行过程
            </span>
            <strong>{visibleAuditEvents.length}</strong>
          </summary>
          <ThinkingTimeline events={events} isRunning={isRunning} result={result} />
        </details>

        {result ? (
          <div className="assistant-answer">
            <div
              className={`agent-completion-status ${isRunning ? "agent-completion-status--streaming" : ""}`}
              aria-label={isRunning ? "Agent 正在流式生成" : "Agent 已完成"}
            >
              <span aria-hidden />
              <strong>{isRunning ? (transport === "sse" ? "SSE 实时输出" : "实时输出") : "已完成"}</strong>
              <small>{isRunning ? "内容持续到达" : `用时 ${durationLabel}`}</small>
            </div>
            <MarkdownRenderer content={result} streaming={isRunning} />
          </div>
        ) : (
          <div className="assistant-answer assistant-answer--pending">
            {isRunning ? (
              <ThinkingLoader durationLabel={durationLabel} phase={agentPhase} />
            ) : (
              "任务完成后会在这里显示最终回复。"
            )}
          </div>
        )}

        <details
          className="thinking-block artifact-block"
          open={files.length > 0}
        >
          <summary>
            <span>
              <FileSearchOutlined aria-hidden />
              输出文件
            </span>
            <strong>{files.length}</strong>
          </summary>
          <ArtifactShelf files={files} />
        </details>
      </div>
    </article>
  );
}

export function ConversationThread({
  onUseExample,
  turns,
  transport,
}: ConversationThreadProps) {
  if (turns.length === 0) {
    return (
      <div className="conversation-empty">
        <div className="empty-manifesto">
          <span className="empty-ordinal">01 — KNOWLEDGE CANVAS</span>
          <h3>让知识连接，<br />让答案浮现。</h3>
          <p>输入目标。KG Agent 会检索、关联并交付可追溯的结果。</p>
        </div>
        <div className="empty-examples">
          <div className="empty-examples-copy">
            <span className="panel-kicker">STARTING POINTS</span>
            <h3>也可以从这些路径开始</h3>
            <p>
              选择一个任务作为草稿，再按你的实际目标调整。
            </p>
          </div>

          <div className="example-grid" aria-label="KG 任务示例">
            {TASK_EXAMPLES.map((example, index) => (
              <button
                className="example-card"
                key={example.tool}
                onClick={() => onUseExample(example.prompt)}
                style={{ "--i": index } as CSSProperties}
                type="button"
              >
                <span className="example-index">{String(index + 1).padStart(2, "0")}</span>
                <span className="example-icon">{example.icon}</span>
                <span className="example-copy">
                  <span>{example.tool}</span>
                  <strong>{example.title}</strong>
                  <small>{example.prompt}</small>
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="conversation-thread" aria-label="聊天消息流">
      {turns.map((turn) => (
        <ConversationTurn key={turn.id} turn={turn} transport={transport} />
      ))}
    </div>
  );
}

const ConversationTurn = memo(function ConversationTurn({
  turn,
  transport,
}: {
  turn: ChatTurn;
  transport: "websocket" | "sse";
}) {
  return (
    <div className="conversation-turn">
      <TurnTelemetry events={turn.events} />
      <article className="chat-message chat-message--user">
        <div className="message-bubble">
          <div className="message-meta">
            <span>你</span>
            <time dateTime={turn.timestamp}>
              {formatTime(turn.timestamp)}
            </time>
          </div>
          <p>{turn.content}</p>
        </div>
        <div className="message-avatar message-avatar--user" aria-hidden>
          你
        </div>
      </article>
      <AssistantMessage
        events={turn.events}
        files={turn.files}
        isRunning={turn.isRunning}
        result={turn.result}
        timestamp={turn.timestamp}
        transport={transport}
      />
    </div>
  );
});
