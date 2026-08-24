const STORAGE_KEY = "kg.thread_id";
const LEGACY_STORAGE_KEY = "deepsearch.thread_id";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function isValidThreadId(value: string | null): value is string {
  return Boolean(value && UUID_PATTERN.test(value));
}

function fallbackUuidV4(): string {
  const bytes = new Uint8Array(16);

  if (globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }

  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;

  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0"));
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10, 16).join("")
  ].join("-");
}

export function createThreadId(): string {
  if (globalThis.crypto?.randomUUID) {
    return globalThis.crypto.randomUUID();
  }

  return fallbackUuidV4();
}

export function getStoredThreadId(): string {
  const existing = window.localStorage.getItem(STORAGE_KEY) || window.localStorage.getItem(LEGACY_STORAGE_KEY);
  if (isValidThreadId(existing)) {
    window.localStorage.setItem(STORAGE_KEY, existing);
    window.localStorage.removeItem(LEGACY_STORAGE_KEY);
    return existing;
  }

  // 旧版本在非安全 HTTP 环境中会生成 manual-*，这里自动迁移为后端要求的 UUID。
  const threadId = createThreadId();
  window.localStorage.setItem(STORAGE_KEY, threadId);
  return threadId;
}

export function storeThreadId(threadId: string): void {
  window.localStorage.setItem(STORAGE_KEY, threadId);
}
