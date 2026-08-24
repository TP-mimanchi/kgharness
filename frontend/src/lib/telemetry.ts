import type { MonitorMessage } from "../types";

function nestedString(value: unknown, key: string): string | null {
  if (!value || typeof value !== "object") return null;
  const candidate = (value as Record<string, unknown>)[key];
  return typeof candidate === "string" && candidate ? candidate : null;
}

export function eventIdentity(event: MonitorMessage): string {
  if (event.event_id) return event.event_id;
  const callId = nestedString(event.data, "call_id")
    || nestedString(event.data, "tool_call_id")
    || nestedString(event.data.args, "tool_call_id");
  if (callId) return `${event.run_id || event.thread_id || "run"}:${event.event}:${callId}`;
  return [
    event.run_id || event.thread_id || "run",
    event.event,
    event.timestamp,
    event.message
  ].join(":");
}

export function uniqueEvents(
  events: MonitorMessage[],
  eventName?: string,
): MonitorMessage[] {
  const seen = new Set<string>();
  return events.filter((event) => {
    if (eventName && event.event !== eventName) return false;
    const identity = eventIdentity(event);
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
}

export function countEvents(events: MonitorMessage[], eventName: string): number {
  return uniqueEvents(events, eventName).length;
}
