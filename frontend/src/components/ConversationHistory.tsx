import {
  DeleteOutlined,
  DownOutlined,
  HistoryOutlined,
  MessageOutlined
} from "@ant-design/icons";
import { Popconfirm } from "antd";
import type { ConversationSummary } from "../types";

interface ConversationHistoryProps {
  conversations: ConversationSummary[];
  activeId: string;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}

function formatConversationTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "--";
  }
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function ConversationHistory({
  conversations,
  activeId,
  onSelect,
  onDelete
}: ConversationHistoryProps) {
  return (
    <details className="conversation-history">
      <summary>
        <span>
          <HistoryOutlined aria-hidden />
          <span>
            <small>HISTORY</small>
            <strong>历史会话</strong>
          </span>
        </span>
        <em>{conversations.length}</em>
        <DownOutlined className="conversation-history-chevron" aria-hidden />
      </summary>
      {conversations.length === 0 ? (
        <div className="conversation-history-empty">暂无历史会话</div>
      ) : (
        <ul className="conversation-history-list">
          {conversations.map((conversation) => (
            <li
              className={
                conversation.id === activeId
                  ? "conversation-history-item conversation-history-item--active"
                  : "conversation-history-item"
              }
              key={conversation.id}
            >
              <button
                className="conversation-history-select"
                onClick={() => onSelect(conversation.id)}
                title={conversation.title}
                type="button"
              >
                <MessageOutlined aria-hidden />
                <span>
                  <strong>{conversation.title}</strong>
                  <small>{formatConversationTime(conversation.updated_at)}</small>
                </span>
              </button>
              <Popconfirm
                cancelText="取消"
                description="将删除聊天记录和 Agent 记忆，生成的文件会保留"
                okText="删除"
                onConfirm={() => onDelete(conversation.id)}
                title="删除该历史会话？"
              >
                <button
                  aria-label={`删除会话 ${conversation.title}`}
                  className="conversation-history-delete"
                  type="button"
                >
                  <DeleteOutlined />
                </button>
              </Popconfirm>
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}
