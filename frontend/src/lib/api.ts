import { API_BASE_URL, TENANT_ID } from "./config";
import type {
  CancelTaskResponse,
  ConversationListResponse,
  ConversationMessagesResponse,
  DeleteConversationResponse,
  FileListResponse,
  IngestionJob,
  KnowledgeBase,
  KnowledgeDocument,
  KnowledgeUploadResponse,
  RetrievalResponse,
  TaskResponse,
  UploadResponse
} from "../types";

function apiUrl(path: string): string {
  return `${API_BASE_URL}${path}`;
}

async function requestJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const message =
      typeof payload === "object" && payload && "detail" in payload
        ? String(payload.detail)
        : `HTTP ${response.status}`;
    throw new Error(message);
  }

  return payload as T;
}

export async function startTask(query: string, threadId: string): Promise<TaskResponse> {
  return requestJson<TaskResponse>(apiUrl("/api/task"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      query,
      thread_id: threadId
    })
  });
}

export async function cancelTask(threadId: string): Promise<CancelTaskResponse> {
  return requestJson<CancelTaskResponse>(apiUrl(`/api/task/${encodeURIComponent(threadId)}/cancel`), {
    method: "POST"
  });
}

export async function uploadSessionFiles(
  files: File[],
  threadId: string
): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("thread_id", threadId);
  files.forEach((file) => formData.append("files", file));

  return requestJson<UploadResponse>(apiUrl("/api/upload"), {
    method: "POST",
    body: formData
  });
}

export async function listSessionFiles(path: string): Promise<FileListResponse> {
  const url = new URL(apiUrl("/api/files"));
  url.searchParams.set("path", path);
  return requestJson<FileListResponse>(url);
}

export function getDownloadUrl(path: string): string {
  const url = new URL(apiUrl("/api/download"));
  url.searchParams.set("path", path);
  return url.toString();
}

export async function fetchConversations(): Promise<ConversationListResponse> {
  return requestJson<ConversationListResponse>(apiUrl("/api/chats"));
}

export async function fetchConversationMessages(
  conversationId: string
): Promise<ConversationMessagesResponse> {
  return requestJson<ConversationMessagesResponse>(
    apiUrl(`/api/chats/${encodeURIComponent(conversationId)}/messages`)
  );
}

export async function deleteConversation(
  conversationId: string
): Promise<DeleteConversationResponse> {
  return requestJson<DeleteConversationResponse>(
    apiUrl(`/api/chats/${encodeURIComponent(conversationId)}`),
    { method: "DELETE" }
  );
}

function ragHeaders(headers?: HeadersInit): Headers {
  const result = new Headers(headers);
  result.set("X-Tenant-ID", TENANT_ID);
  return result;
}

export async function listKnowledgeBases(): Promise<KnowledgeBase[]> {
  return requestJson<KnowledgeBase[]>(apiUrl("/api/v1/knowledge-bases"), {
    headers: ragHeaders()
  });
}

export async function createKnowledgeBase(
  name: string,
  description: string
): Promise<KnowledgeBase> {
  return requestJson<KnowledgeBase>(apiUrl("/api/v1/knowledge-bases"), {
    method: "POST",
    headers: ragHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ name, description })
  });
}

export async function listKnowledgeDocuments(
  knowledgeBaseId: string
): Promise<KnowledgeDocument[]> {
  return requestJson<KnowledgeDocument[]>(
    apiUrl(`/api/v1/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents`),
    { headers: ragHeaders() }
  );
}

export async function uploadKnowledgeDocument(
  knowledgeBaseId: string,
  file: File
): Promise<KnowledgeUploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  return requestJson<KnowledgeUploadResponse>(
    apiUrl(`/api/v1/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents`),
    {
      method: "POST",
      headers: ragHeaders(),
      body: formData
    }
  );
}

export async function retryIngestionJob(jobId: string): Promise<IngestionJob> {
  return requestJson<IngestionJob>(
    apiUrl(`/api/v1/ingestion-jobs/${encodeURIComponent(jobId)}/retry`),
    { method: "POST", headers: ragHeaders() }
  );
}

export async function searchKnowledge(
  query: string,
  knowledgeBaseIds: string[]
): Promise<RetrievalResponse> {
  return requestJson<RetrievalResponse>(apiUrl("/api/v1/retrieval/search"), {
    method: "POST",
    headers: ragHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      query,
      knowledge_base_ids: knowledgeBaseIds,
      top_k: 8,
      debug: false
    })
  });
}
