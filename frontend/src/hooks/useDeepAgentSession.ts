import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { cancelTask, listSessionFiles, startTask, uploadSessionFiles } from "../lib/api";
import { apiEventStreamUrl, WS_BASE_URL } from "../lib/config";
import { createThreadId, getStoredThreadId, storeThreadId } from "../lib/thread";
import type {
  ConnectionState,
  MonitorMessage,
  OutputFile,
  SocketMessage,
  UploadedItem
} from "../types";

const MAX_EVENTS = 120;

function extractString(data: Record<string, unknown>, key: string): string | null {
  const value = data[key];
  return typeof value === "string" ? value : null;
}

export function useDeepAgentSession() {
  const socketRef = useRef<WebSocket | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectTimerRef = useRef<number | undefined>(undefined);
  const heartbeatTimerRef = useRef<number | undefined>(undefined);
  const uploadedNameSetRef = useRef<Set<string>>(new Set());
  const [threadId, setThreadId] = useState(getStoredThreadId);
  const [currentRunId, setCurrentRunId] = useState("");
  const [transport, setTransport] = useState<"websocket" | "sse">("websocket");
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
  const [events, setEvents] = useState<MonitorMessage[]>([]);
  const [files, setFiles] = useState<OutputFile[]>([]);
  const [sessionPath, setSessionPath] = useState("");
  const [result, setResult] = useState("");
  const [lastError, setLastError] = useState("");
  const [lastPongAt, setLastPongAt] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadedItems, setUploadedItems] = useState<UploadedItem[]>([]);

  const clearSocketTimers = useCallback(() => {
    if (reconnectTimerRef.current) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = undefined;
    }
    if (heartbeatTimerRef.current) {
      window.clearInterval(heartbeatTimerRef.current);
      heartbeatTimerRef.current = undefined;
    }
  }, []);

  const switchToThread = useCallback(
    (
      nextThreadId: string,
      seed?: {
        events?: MonitorMessage[];
        files?: OutputFile[];
        result?: string;
        isRunning?: boolean;
        sessionPath?: string;
      }
    ) => {
      storeThreadId(nextThreadId);
      setThreadId(nextThreadId);
      eventSourceRef.current?.close();
      setCurrentRunId("");
      setTransport("websocket");
      // 恢复历史会话时用该会话最后一轮的数据做种子：
      // 后续文件轮询等触发的同步 effect 写回的是相同内容，不会覆盖恢复结果
      setEvents(seed?.events ?? []);
      setFiles(seed?.files ?? []);
      setSessionPath(seed?.sessionPath ?? "");
      setResult(seed?.result ?? "");
      setLastError("");
      setUploadedItems([]);
      uploadedNameSetRef.current.clear();
      setIsRunning(seed?.isRunning ?? false);
      setIsCancelling(false);
    },
    []
  );

  const resetSession = useCallback(() => {
    switchToThread(createThreadId());
  }, [switchToThread]);

  const refreshFiles = useCallback(async () => {
    if (!sessionPath) {
      return;
    }

    const response = await listSessionFiles(sessionPath);
    if (response.error) {
      throw new Error(response.error);
    }
    setFiles(response.files || []);
  }, [sessionPath]);

  const processMonitorEvent = useCallback((payload: MonitorMessage) => {
    setLastPongAt(new Date().toISOString());

    if (payload.event === "message_delta") {
      const delta = extractString(payload.data, "delta");
      if (delta) {
        setResult((previous) => previous + delta);
      }
      return;
    }

    // Reasoning deltas are intentionally not rendered as hidden chain-of-thought.
    // The timeline receives only auditable lifecycle summaries and tool activity.
    if (payload.event !== "reasoning_delta") {
      setEvents((previous) => [...previous, payload].slice(-MAX_EVENTS));
    }

    if (payload.event === "session_created") {
      const path = extractString(payload.data, "path");
      if (path) {
        setSessionPath(path);
      }
    }

    if (payload.event === "task_result") {
      const finalResult = extractString(payload.data, "result");
      setResult(finalResult || payload.message);
    }

    if (payload.event === "task_cancelled" || payload.event === "run_cancelled") {
      setResult((previous) => previous || payload.message);
      setIsRunning(false);
      setIsCancelling(false);
    }

    if (payload.event === "error" || payload.event === "run_failed") {
      const detail = extractString(payload.data, "error");
      setLastError(detail || payload.message);
      setIsRunning(false);
      setIsCancelling(false);
    }

    if (payload.event === "run_completed") {
      setIsRunning(false);
      setIsCancelling(false);
    }
  }, []);

  useEffect(() => {
    if (currentRunId) {
      return;
    }
    let disposed = false;

    function connect() {
      clearSocketTimers();
      const hadSocket = Boolean(socketRef.current);
      socketRef.current?.close();
      setConnectionState(hadSocket ? "reconnecting" : "connecting");

      const socket = new WebSocket(`${WS_BASE_URL}/ws/${encodeURIComponent(threadId)}`);
      socketRef.current = socket;

      socket.onopen = () => {
        if (disposed) {
          return;
        }
        setConnectionState("connected");
        setLastError("");
        heartbeatTimerRef.current = window.setInterval(() => {
          if (socket.readyState === WebSocket.OPEN) {
            socket.send("ping");
          }
        }, 25000);
      };

      socket.onmessage = (event) => {
        if (socketRef.current !== socket) {
          return;
        }
        try {
          const payload = JSON.parse(event.data) as SocketMessage;
          if (payload.type === "pong") {
            setLastPongAt(new Date().toISOString());
            return;
          }

          if (payload.type !== "monitor_event") {
            return;
          }

          processMonitorEvent(payload);
        } catch (error) {
          setLastError(error instanceof Error ? error.message : "WebSocket 消息解析失败");
        }
      };

      socket.onerror = () => {
        if (!disposed && socketRef.current === socket) {
          setLastError("WebSocket 连接异常，请确认后端服务已启动");
        }
      };

      socket.onclose = () => {
        if (socketRef.current !== socket) {
          return;
        }
        clearSocketTimers();
        if (disposed) {
          setConnectionState("closed");
          return;
        }
        setConnectionState("reconnecting");
        reconnectTimerRef.current = window.setTimeout(connect, 2000);
      };
    }

    connect();

    return () => {
      disposed = true;
      clearSocketTimers();
      socketRef.current?.close();
    };
  }, [clearSocketTimers, currentRunId, processMonitorEvent, threadId]);

  useEffect(() => {
    if (!currentRunId) {
      return;
    }

    clearSocketTimers();
    socketRef.current?.close();
    setTransport("sse");
    setConnectionState("connecting");
    const source = new EventSource(
      apiEventStreamUrl(`/api/runs/${encodeURIComponent(currentRunId)}/events`)
    );
    eventSourceRef.current = source;

    source.onopen = () => {
      setConnectionState("connected");
      setLastError("");
    };
    source.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as MonitorMessage;
        processMonitorEvent(payload);
        if (["run_completed", "run_failed", "run_cancelled"].includes(payload.event)) {
          source.close();
          setConnectionState("closed");
        }
      } catch (error) {
        setLastError(error instanceof Error ? error.message : "SSE 消息解析失败");
      }
    };
    source.onerror = () => {
      if (source.readyState === EventSource.CLOSED) {
        setConnectionState("closed");
      } else {
        setConnectionState("reconnecting");
      }
    };

    return () => {
      source.close();
      if (eventSourceRef.current === source) {
        eventSourceRef.current = null;
      }
    };
  }, [clearSocketTimers, currentRunId, processMonitorEvent]);

  useEffect(() => {
    if (!sessionPath) {
      return;
    }

    refreshFiles().catch((error: unknown) => {
      setLastError(error instanceof Error ? error.message : "文件列表刷新失败");
    });

    const timer = window.setInterval(() => {
      refreshFiles().catch((error: unknown) => {
        setLastError(error instanceof Error ? error.message : "文件列表刷新失败");
      });
    }, isRunning ? 2500 : 6000);

    return () => window.clearInterval(timer);
  }, [isRunning, refreshFiles, sessionPath]);

  const submitTask = useCallback(
    async (query: string) => {
      const cleanQuery = query.trim();
      if (!cleanQuery) {
        throw new Error("请输入 KG 任务");
      }

      setIsRunning(true);
      setIsCancelling(false);
      setEvents([]);
      setResult("");
      setLastError("");
      try {
        const response = await startTask(cleanQuery, threadId);
        if (response.thread_id && response.thread_id !== threadId) {
          storeThreadId(response.thread_id);
          setThreadId(response.thread_id);
        }
        if (response.execution_mode === "distributed") {
          setCurrentRunId(response.run_id);
          setTransport("sse");
        }
        return response;
      } catch (error) {
        setIsRunning(false);
        setIsCancelling(false);
        throw error;
      }
    },
    [threadId]
  );

  const cancelCurrentTask = useCallback(async () => {
    if (!isRunning) {
      throw new Error("当前没有正在执行的任务");
    }

    setIsCancelling(true);
    setLastError("");
    try {
      const response = await cancelTask(threadId);
      const acceptedAt = new Date().toISOString();
      const cancelEvent: MonitorMessage = {
        type: "monitor_event",
        event: "cancel_requested",
        message: "取消请求已确认，后台正在停止当前调用",
        data: { status: response.status },
        run_id: response.run_id || currentRunId || null,
        thread_id: threadId,
        timestamp: acceptedAt
      };
      setEvents((previous) => [
        ...previous,
        cancelEvent
      ].slice(-MAX_EVENTS));
      // 取消一经 API 接受就立即释放输入区；后台终止事件仍会继续同步并落库。
      setIsRunning(false);
      setIsCancelling(false);
      setResult((previous) => previous || "任务已取消");
      return response;
    } catch (error) {
      setIsCancelling(false);
      throw error;
    }
  }, [currentRunId, isRunning, threadId]);

  const uploadFiles = useCallback(
    async (items: UploadedItem[]) => {
      if (items.length === 0) {
        throw new Error("请选择要上传的文件");
      }

      const nextItems = items.filter((item) => !uploadedNameSetRef.current.has(item.name));

      if (nextItems.length === 0) {
        return {
          status: "uploaded",
          files: Array.from(uploadedNameSetRef.current)
        };
      }

      setIsUploading(true);
      setLastError("");
      try {
        const response = await uploadSessionFiles(
          nextItems.map((item) => item.raw),
          threadId
        );
        setUploadedItems((previous) => {
          const names = new Set(previous.map((item) => item.name));
          const next = [...previous];
          nextItems.forEach((item) => {
            if (!names.has(item.name)) {
              names.add(item.name);
              uploadedNameSetRef.current.add(item.name);
              next.push(item);
            }
          });
          return next;
        });
        return response;
      } finally {
        setIsUploading(false);
      }
    },
    [threadId]
  );

  const stats = useMemo(() => {
    const toolEvents = events.filter((event) => event.event === "tool_start").length;
    const assistantEvents = events.filter((event) => event.event === "assistant_call").length;
    const errorEvents = events.filter((event) => event.event === "error").length;

    return {
      toolEvents,
      assistantEvents,
      errorEvents,
      fileCount: files.length
    };
  }, [events, files.length]);

  return {
    connectionState,
    currentRunId,
    events,
    files,
    isCancelling,
    isRunning,
    isUploading,
    lastError,
    lastPongAt,
    refreshFiles,
    resetSession,
    result,
    sessionPath,
    stats,
    cancelCurrentTask,
    submitTask,
    switchToThread,
    threadId,
    transport,
    uploadFiles,
    uploadedItems
  };
}
