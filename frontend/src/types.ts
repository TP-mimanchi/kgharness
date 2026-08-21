export type ConnectionState = "connecting" | "connected" | "reconnecting" | "closed";

export type MonitorEventName =
  | "session_created"
  | "tool_start"
  | "assistant_call"
  | "model_call"
  | "task_result"
  | "task_cancelled"
  | "error"
  | string;

export interface MonitorMessage {
  type: "monitor_event";
  event: MonitorEventName;
  message: string;
  data: Record<string, unknown>;
  run_id?: string | null;
  thread_id?: string | null;
  timestamp: string;
}

export interface PongMessage {
  type: "pong";
  message: string;
}

export type SocketMessage = MonitorMessage | PongMessage;

export interface TaskResponse {
  status: "started" | string;
  thread_id: string;
  run_id: string;
  execution_mode: "local" | "distributed" | string;
  events_url: string;
}

export interface CancelTaskResponse {
  status: "cancelled" | "cancelling" | string;
  thread_id: string;
  run_id?: string;
  message?: string;
}

export interface UploadResponse {
  status: "uploaded" | string;
  files: string[];
}

export interface OutputFile {
  name: string;
  type: "file" | string;
  path: string;
  size: number;
  mtime: number;
}

export interface FileListResponse {
  files?: OutputFile[];
  error?: string;
}

export interface UploadedItem {
  uid: string;
  name: string;
  size: number;
  raw: File;
}

export interface KnowledgeBase {
  id: string;
  tenant_id: string;
  name: string;
  description: string;
  status: string;
  document_count: number;
  created_at: string;
}

export interface IngestionJob {
  id: string;
  document_id: string;
  status: string;
  stage: string;
  attempt: number;
  progress: number;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeDocument {
  id: string;
  knowledge_base_id: string;
  original_filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  status: string;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  latest_job: IngestionJob | null;
}

export interface KnowledgeUploadResponse {
  created: boolean;
  document: Omit<KnowledgeDocument, "latest_job">;
  job: IngestionJob;
}

export interface RetrievalEvidence {
  citation_id: string;
  filename: string;
  content: string;
  page_start: number | null;
  page_end: number | null;
}

export interface RetrievalResponse {
  query_id: string;
  query: string;
  evidence: RetrievalEvidence[];
  insufficient_evidence: boolean;
  timings: { total_ms: number } & Record<string, number>;
  warnings: string[];
}

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ChatMessageRecord {
  id: string;
  role: "user" | "assistant" | string;
  content: string;
  events: MonitorMessage[];
  files: OutputFile[];
  created_at: string;
}

export interface ConversationListResponse {
  chats: ConversationSummary[];
}

export interface ConversationMessagesResponse {
  messages: ChatMessageRecord[];
}

export interface DeleteConversationResponse {
  status: "deleted" | string;
  thread_id: string;
}
