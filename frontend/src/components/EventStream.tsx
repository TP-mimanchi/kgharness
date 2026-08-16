import {
  BranchesOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  FileSearchOutlined,
  ToolOutlined
} from "@ant-design/icons";
import { Empty } from "antd";
import type { MonitorMessage } from "../types";

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "--:--:--";
  }
  return date.toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  });
}

function EventIcon({ event }: { event: string }) {
  if (event === "assistant_call") {
    return <BranchesOutlined aria-hidden />;
  }
  if (event === "tool_start") {
    return <ToolOutlined aria-hidden />;
  }
  if (event === "session_created") {
    return <FileSearchOutlined aria-hidden />;
  }
  if (event === "task_result") {
    return <CheckCircleOutlined aria-hidden />;
  }
  if (event === "error") {
    return <CloseCircleOutlined aria-hidden />;
  }
  return <ClockCircleOutlined aria-hidden />;
}

interface EventStreamProps {
  events: MonitorMessage[];
}

export function EventStream({ events }: EventStreamProps) {
  return (
    <section className="console-panel event-panel" aria-labelledby="event-title">
      <div className="panel-heading">
        <div>
          <span className="panel-kicker">LIVE TRACE</span>
          <h2 id="event-title">实时执行轨迹</h2>
        </div>
        <span className="event-count">{events.length}</span>
      </div>

      {events.length === 0 ? (
        <div className="empty-console">
          <Empty
            description="等待 WebSocket 推送任务事件"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        </div>
      ) : (
        <ol className="event-stream">
          {events.map((event, index) => (
            <li className={`event-row event-row--${event.event}`} key={`${event.timestamp}-${index}`}>
              <div className="event-icon">
                <EventIcon event={event.event} />
              </div>
              <div className="event-body">
                <div className="event-meta">
                  <span>{event.event}</span>
                  <time dateTime={event.timestamp}>{formatTime(event.timestamp)}</time>
                </div>
                <p>{event.message}</p>
                {Object.keys(event.data).length > 0 ? (
                  <pre>{JSON.stringify(event.data, null, 2)}</pre>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
