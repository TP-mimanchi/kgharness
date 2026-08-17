import {
  ApiOutlined,
  BookOutlined,
  BranchesOutlined,
  CloseCircleOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  DownOutlined,
  FileSearchOutlined,
  MessageOutlined,
  ToolOutlined
} from "@ant-design/icons";
import { Alert, App as AntApp, Button } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { ChatComposer } from "./components/ChatComposer";
import { ConversationHistory } from "./components/ConversationHistory";
import { ConversationThread } from "./components/ConversationThread";
import { KnowledgeBasePage } from "./components/KnowledgeBasePage";
import { ParticleField } from "./components/ParticleField";
import type { ChatTurn } from "./components/ConversationThread";
import { API_BASE_URL, WS_BASE_URL } from "./lib/config";
import {
  deleteConversation,
  fetchConversationMessages,
  fetchConversations
} from "./lib/api";
import { useDeepAgentSession } from "./hooks/useDeepAgentSession";
import type {
  ChatMessageRecord,
  ConnectionState,
  ConversationSummary,
  UploadedItem
} from "./types";

function connectionLabel(state: ConnectionState): string {
  const labels: Record<ConnectionState, string> = {
    connecting: "连接中",
    connected: "已连接",
    reconnecting: "重连中",
    closed: "已关闭"
  };
  return labels[state];
}

function createTurn(content: string, threadId: string): ChatTurn {
  return {
    id: crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}`,
    threadId,
    content,
    events: [],
    files: [],
    isRunning: true,
    result: "",
    timestamp: new Date().toISOString()
  };
}

// 数据库消息按 role 配对还原成 ChatTurn：user 开一轮，assistant 回填结果。
// 末条 user 无配对 assistant 时保留 isRunning=true，后台任务后续事件会经 WebSocket 流入
function buildTurns(messages: ChatMessageRecord[], threadId: string): ChatTurn[] {
  const turns: ChatTurn[] = [];
  for (const message of messages) {
    if (message.role === "user") {
      turns.push({
        id: message.id,
        threadId,
        content: message.content,
        events: [],
        files: [],
        isRunning: true,
        result: "",
        timestamp: message.created_at
      });
      continue;
    }
    const lastTurn = turns[turns.length - 1];
    if (!lastTurn || !lastTurn.isRunning) {
      continue;
    }
    lastTurn.events = message.events;
    lastTurn.files = message.files;
    lastTurn.isRunning = false;
    lastTurn.result = message.content;
  }
  return turns;
}

// 从消息的事件流里找最近一次 session_created 的路径，恢复后文件轮询可以继续工作
function extractSessionPath(messages: ChatMessageRecord[]): string {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const events = messages[index].events;
    for (let eventIndex = events.length - 1; eventIndex >= 0; eventIndex -= 1) {
      const event = events[eventIndex];
      if (event.event !== "session_created") {
        continue;
      }
      const path = event.data.path;
      if (typeof path === "string" && path) {
        return path;
      }
    }
  }
  return "";
}

export default function App() {
  const { message } = AntApp.useApp();
  const [query, setQuery] = useState("");
  const [stagedItems, setStagedItems] = useState<UploadedItem[]>([]);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activePage, setActivePage] = useState<"chat" | "knowledge">("chat");
  const streamRef = useRef<HTMLElement | null>(null);
  const session = useDeepAgentSession();

  // 恢复历史会话时置 true，跳过一次同步 effect，防止 switchToThread 清空 session 状态后
  // 把空值写进刚恢复的最后一轮消息
  const restoreGuardRef = useRef(false);
  const prevIsRunningRef = useRef(session.isRunning);

  const refreshConversations = useCallback(async () => {
    try {
      const response = await fetchConversations();
      setConversations(response.chats);
    } catch {
      // 列表刷新失败不打断聊天主流程
    }
  }, []);

  useEffect(() => {
    refreshConversations();
  }, [refreshConversations]);

  useEffect(() => {
    const wasRunning = prevIsRunningRef.current;
    prevIsRunningRef.current = session.isRunning;
    if (wasRunning && !session.isRunning) {
      // 任务结束时刷新列表，拿到最新的标题和更新时间
      refreshConversations();
    }
  }, [refreshConversations, session.isRunning]);

  useEffect(() => {
    setTurns((previous) => {
      if (restoreGuardRef.current) {
        restoreGuardRef.current = false;
        return previous;
      }

      if (previous.length === 0) {
        return previous;
      }

      const latestTurn = previous[previous.length - 1];
      if (latestTurn.threadId !== session.threadId) {
        return previous;
      }

      const nextLatestTurn = {
        ...latestTurn,
        events: session.events,
        files: session.files,
        isRunning: session.isRunning,
        result: session.result
      };

      return [...previous.slice(0, -1), nextLatestTurn];
    });
  }, [session.events, session.files, session.isRunning, session.result, session.threadId]);

  useEffect(() => {
    if (turns.length === 0) {
      return;
    }

    const streamNode = streamRef.current;
    if (!streamNode) {
      return;
    }

    window.requestAnimationFrame(() => {
      streamNode.scrollTo({
        top: streamNode.scrollHeight,
        behavior: "smooth"
      });
    });
  }, [turns]);

  async function handleSubmit() {
    const cleanQuery = query.trim();
    if (!cleanQuery) {
      message.warning("请输入研搜任务");
      return;
    }

    const nextTurn = createTurn(cleanQuery, session.threadId);
    setTurns((previous) => [...previous, nextTurn]);
    setQuery("");

    try {
      const response = await session.submitTask(cleanQuery);
      // 后端理论上前端传入合法 UUID 时会原样返回；若返回了新 thread_id，修正本轮归属
      if (response.thread_id !== nextTurn.threadId) {
        setTurns((previous) =>
          previous.map((turn) =>
            turn.id === nextTurn.id ? { ...turn, threadId: response.thread_id } : turn
          )
        );
      }
      refreshConversations();
      message.success("任务已启动，执行过程会显示在对话中");
    } catch (error) {
      setTurns((previous) =>
        previous.map((turn) =>
          turn.id === nextTurn.id
            ? {
                ...turn,
                isRunning: false,
                result: error instanceof Error ? error.message : "任务启动失败"
              }
            : turn
        )
      );
      message.error(error instanceof Error ? error.message : "任务启动失败");
    }
  }

  async function handleCancel() {
    try {
      const response = await session.cancelCurrentTask();
      message.info(response.status === "cancelling" ? "取消请求已发送，正在等待当前调用结束" : "任务已取消");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "取消任务失败");
    }
  }

  async function handleUpload(items: UploadedItem[]) {
    try {
      const response = await session.uploadFiles(items);
      setStagedItems([]);
      message.success(`已上传 ${response.files.length} 个文件`);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "上传失败");
    }
  }

  function handleNewSession() {
    session.resetSession();
    setTurns([]);
    setQuery("");
    setStagedItems([]);
    setActivePage("chat");
  }

  async function handleSelectConversation(id: string) {
    if (id === session.threadId && turns.length > 0) {
      setActivePage("chat");
      return;
    }

    try {
      const response = await fetchConversationMessages(id);
      const restoredTurns = buildTurns(response.messages, id);
      const restoredPath = extractSessionPath(response.messages);
      const lastTurn = restoredTurns[restoredTurns.length - 1];

      // 先切 thread，把最后一轮数据作为种子写入 session 状态：
      // restoreGuard 跳过紧随其后的第一次同步，之后文件轮询触发的同步
      // 写回的也是种子内容，不会覆盖恢复出来的消息
      restoreGuardRef.current = true;
      session.switchToThread(id, {
        events: lastTurn?.events ?? [],
        files: lastTurn?.files ?? [],
        result: lastTurn?.result ?? "",
        isRunning: Boolean(lastTurn?.isRunning),
        sessionPath: restoredPath
      });
      setTurns(restoredTurns);
      setQuery("");
      setStagedItems([]);
      setActivePage("chat");
    } catch (error) {
      message.error(
        error instanceof Error ? error.message : "加载历史会话失败"
      );
    }
  }

  async function handleDeleteConversation(id: string) {
    try {
      await deleteConversation(id);
      message.success("会话已删除");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "删除会话失败");
      return;
    }
    await refreshConversations();
    // 删除的是当前会话时，回到一个全新的空白会话
    if (id === session.threadId) {
      handleNewSession();
    }
  }

  const online = session.connectionState === "connected";
  return (
    <div className="chat-app-shell min-h-dvh">
      <aside className="chat-sidebar" aria-label="会话信息">
        <div className="sidebar-brand">
          <span className="brand-mark" aria-hidden>深</span>
          <div>
            <span className="panel-kicker">DEEPSEARCH</span>
            <h1>深度研搜</h1>
          </div>
        </div>

        <button className="workspace-switcher" type="button">
          <span className={`workspace-signal ${online ? "workspace-signal--online" : ""}`} />
          <span>
            <small>当前工作区</small>
            <strong>研究实验室</strong>
          </span>
          <em>{connectionLabel(session.connectionState)}</em>
        </button>

        <nav className="sidebar-nav" aria-label="主要功能">
          <button
            className={activePage === "chat" ? "sidebar-nav-item sidebar-nav-item--active" : "sidebar-nav-item"}
            onClick={() => setActivePage("chat")}
            type="button"
          >
            <MessageOutlined />
            智能研搜
          </button>
          <button
            className={activePage === "knowledge" ? "sidebar-nav-item sidebar-nav-item--active" : "sidebar-nav-item"}
            onClick={() => setActivePage("knowledge")}
            type="button"
          >
            <BookOutlined />
            知识库管理
          </button>
        </nav>

        <Button className="new-chat-button" block onClick={handleNewSession}>＋ 新建研搜</Button>

        <ConversationHistory
          activeId={session.threadId}
          conversations={conversations}
          onDelete={handleDeleteConversation}
          onSelect={handleSelectConversation}
        />

        <div className="sidebar-status-list">
          <div className={`sidebar-status ${online ? "sidebar-status--online" : "sidebar-status--warn"}`}>
            <ApiOutlined aria-hidden />
            <span>WebSocket</span>
            <strong>{connectionLabel(session.connectionState)}</strong>
          </div>
          <div className="sidebar-status">
            <BranchesOutlined aria-hidden />
            <span>助手调度</span>
            <strong>{session.stats.assistantEvents}</strong>
          </div>
          <div className="sidebar-status">
            <ToolOutlined aria-hidden />
            <span>工具调用</span>
            <strong>{session.stats.toolEvents}</strong>
          </div>
          <div className={session.stats.errorEvents > 0 ? "sidebar-status sidebar-status--error" : "sidebar-status"}>
            <CloseCircleOutlined aria-hidden />
            <span>异常</span>
            <strong>{session.stats.errorEvents}</strong>
          </div>
        </div>

        <details className="agent-selector">
          <summary>
            <span>
              <BranchesOutlined aria-hidden />
              <span>
                <small>SUB AGENTS</small>
                <strong>子智能体</strong>
              </span>
            </span>
            <em>3</em>
            <DownOutlined className="agent-selector-chevron" aria-hidden />
          </summary>
          <ul className="agent-mini-list">
            <li>
              <CloudServerOutlined aria-hidden />
              <span><strong>网络搜索助手</strong><small>公开信息检索</small></span>
            </li>
            <li>
              <DatabaseOutlined aria-hidden />
              <span><strong>数据库查询助手</strong><small>结构化业务数据</small></span>
            </li>
            <li>
              <FileSearchOutlined aria-hidden />
              <span><strong>企业知识库助手</strong><small>内部文档证据</small></span>
            </li>
          </ul>
        </details>

        <div className="sidebar-section sidebar-endpoints">
          <span className="sidebar-label">SESSION · {session.threadId.slice(0, 8)}</span>
          <code title={API_BASE_URL}>API connected</code>
          <code title={WS_BASE_URL}>Realtime channel ready</code>
        </div>
      </aside>

      {activePage === "knowledge" ? <KnowledgeBasePage /> : <main className="chat-main">
        <ParticleField />
        <header className="chat-topbar">
          <div>
            <span className="panel-kicker">DEEPSEARCH / LIVE</span>
            <h2>研究工作台</h2>
          </div>
          <div className="topbar-meta">
            <span>THREAD</span>
            <strong>{session.threadId.slice(0, 8)}</strong>
          </div>
        </header>

        {session.lastError ? (
          <Alert
            className="chat-alert"
            message={session.lastError}
            showIcon
            type="error"
          />
        ) : null}

        <section className="chat-stream-panel" ref={streamRef}>
          <ConversationThread
            onUseExample={setQuery}
            turns={turns}
          />
        </section>

        <ChatComposer
          isCancelling={session.isCancelling}
          isRunning={session.isRunning}
          isUploading={session.isUploading}
          onCancel={handleCancel}
          onNewSession={handleNewSession}
          onQueryChange={setQuery}
          onStagedItemsChange={setStagedItems}
          onSubmit={handleSubmit}
          onUpload={handleUpload}
          query={query}
          stagedItems={stagedItems}
          uploadedItems={session.uploadedItems}
        />
      </main>}
    </div>
  );
}
