import {
  ApiOutlined,
  BranchesOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  FileDoneOutlined,
  ToolOutlined
} from "@ant-design/icons";
import type { ConnectionState } from "../types";

interface StatusStripProps {
  connectionState: ConnectionState;
  isRunning: boolean;
  stats: {
    toolEvents: number;
    assistantEvents: number;
    errorEvents: number;
    fileCount: number;
  };
}

function connectionLabel(state: ConnectionState): string {
  const labels: Record<ConnectionState, string> = {
    connecting: "连接中",
    connected: "已连接",
    reconnecting: "重连中",
    closed: "已关闭"
  };
  return labels[state];
}

export function StatusStrip({ connectionState, isRunning, stats }: StatusStripProps) {
  const online = connectionState === "connected";

  return (
    <section className="status-strip" aria-label="任务状态">
      <div className={`metric-tile ${online ? "metric-tile--online" : "metric-tile--warn"}`}>
        <ApiOutlined aria-hidden />
        <div>
          <span>WebSocket</span>
          <strong>{connectionLabel(connectionState)}</strong>
        </div>
      </div>
      <div className={`metric-tile ${isRunning ? "metric-tile--live" : ""}`}>
        {isRunning ? <BranchesOutlined aria-hidden /> : <CheckCircleOutlined aria-hidden />}
        <div>
          <span>任务态</span>
          <strong>{isRunning ? "执行中" : "待命"}</strong>
        </div>
      </div>
      <div className="metric-tile">
        <ToolOutlined aria-hidden />
        <div>
          <span>工具调用</span>
          <strong>{stats.toolEvents}</strong>
        </div>
      </div>
      <div className="metric-tile">
        <BranchesOutlined aria-hidden />
        <div>
          <span>助手调度</span>
          <strong>{stats.assistantEvents}</strong>
        </div>
      </div>
      <div className="metric-tile">
        <FileDoneOutlined aria-hidden />
        <div>
          <span>产物</span>
          <strong>{stats.fileCount}</strong>
        </div>
      </div>
      <div className={`metric-tile ${stats.errorEvents > 0 ? "metric-tile--error" : ""}`}>
        <CloseCircleOutlined aria-hidden />
        <div>
          <span>异常</span>
          <strong>{stats.errorEvents}</strong>
        </div>
      </div>
    </section>
  );
}
