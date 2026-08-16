const STORAGE_KEY = "deepsearch.thread_id";

export function createThreadId(): string {
  if (crypto.randomUUID) {
    return crypto.randomUUID();
  }

  return `manual-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function getStoredThreadId(): string {
  const existing = window.localStorage.getItem(STORAGE_KEY);
  if (existing) {
    return existing;
  }

  const threadId = createThreadId();
  window.localStorage.setItem(STORAGE_KEY, threadId);
  return threadId;
}

export function storeThreadId(threadId: string): void {
  window.localStorage.setItem(STORAGE_KEY, threadId);
}
