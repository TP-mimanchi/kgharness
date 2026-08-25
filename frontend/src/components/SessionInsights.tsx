import { ArrowDownOutlined, ArrowUpOutlined, BranchesOutlined, ToolOutlined } from "@ant-design/icons";
import { memo } from "react";
import type { CSSProperties } from "react";
import { countEvents, uniqueEvents } from "../lib/telemetry";
import type { MonitorMessage } from "../types";
import type { ChatTurn } from "./ConversationThread";

interface SessionInsightsProps {
  turns: ChatTurn[];
  isRunning: boolean;
}

interface TokenPoint {
  cumulativeInput: number;
  cumulativeOutput: number;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function cumulativeUsage(events: MonitorMessage[]): TokenPoint[] {
  let cumulativeInput = 0;
  let cumulativeOutput = 0;
  const points: TokenPoint[] = [];
  for (const event of uniqueEvents(events, "model_usage")) {
    const input = finiteNumber(event.data.input_tokens) ?? 0;
    const output = finiteNumber(event.data.output_tokens) ?? 0;
    if (input + output <= 0) continue;
    cumulativeInput += input;
    cumulativeOutput += output;
    points.push({ cumulativeInput, cumulativeOutput });
  }
  return points;
}

function compact(value: number): string {
  if (value >= 1000) return `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}k`;
  return String(value);
}

function points(values: number[], width: number, height: number, maxValue: number): string {
  const max = Math.max(maxValue, 1);
  const step = values.length > 1 ? width / (values.length - 1) : 0;
  return values.map((value, index) => `${index * step},${height - (value / max) * (height - 14) - 7}`).join(" ");
}

function sameInsightInputs(
  previous: SessionInsightsProps,
  next: SessionInsightsProps,
): boolean {
  if (previous.isRunning !== next.isRunning || previous.turns.length !== next.turns.length) {
    return false;
  }
  return previous.turns.every((turn, index) => {
    const nextTurn = next.turns[index];
    return turn.events === nextTurn.events
      && turn.isRunning === nextTurn.isRunning
      && (turn.isRunning || turn.result === nextTurn.result);
  });
}

export const SessionInsights = memo(function SessionInsights({ turns, isRunning }: SessionInsightsProps) {
  const allEvents = turns.flatMap((turn) => turn.events);
  const usagePoints = cumulativeUsage(allEvents);
  const chartData = usagePoints.slice(-10);
  const visibleData = chartData.length > 0 ? chartData : [{ cumulativeInput: 0, cumulativeOutput: 0 }];
  const inputs = visibleData.map((item) => item.cumulativeInput);
  const outputs = visibleData.map((item) => item.cumulativeOutput);
  const tokenScaleMax = Math.max(...inputs, ...outputs, 1);
  const totals = usagePoints[usagePoints.length - 1];
  const totalInput = totals?.cumulativeInput ?? 0;
  const totalOutput = totals?.cumulativeOutput ?? 0;
  const tools = countEvents(allEvents, "tool_start");
  const agents = countEvents(allEvents, "assistant_call");
  const hasActual = usagePoints.length > 0;
  const firstVisibleCall = Math.max(1, usagePoints.length - chartData.length + 1);
  const success = turns.length === 0 ? 100 : Math.round((turns.filter((turn) => !turn.isRunning && Boolean(turn.result)).length / turns.length) * 100);

  return (
    <aside className="session-insights" aria-label="会话统计">
      <header className="insights-header">
        <div>
          <span className="panel-kicker">SESSION PULSE</span>
          <h2>会话统计</h2>
        </div>
        <span className={isRunning ? "live-indicator live-indicator--active" : "live-indicator"}>
          <i />{isRunning ? "LIVE" : "IDLE"}
        </span>
      </header>

      <section className="token-summary" aria-label="Token 总览">
        <div className="token-summary-title">
          <span>Token 总览</span>
          <em>{hasActual ? "API" : "等待数据"}</em>
        </div>
        <strong>{compact(totalInput + totalOutput)}</strong>
        <small>{usagePoints.length} 次模型调用累计</small>
        <div className="token-split">
          <span><ArrowDownOutlined />输入 <b>{compact(totalInput)}</b></span>
          <span><ArrowUpOutlined />输出 <b>{compact(totalOutput)}</b></span>
        </div>
      </section>

      <section className="token-chart" aria-label="每次模型调用 Token 累计变化">
        <div className="insight-section-heading">
          <div><strong>调用累计</strong><small>最近 {Math.max(chartData.length, 1)} 次模型调用</small></div>
          <div className="chart-legend"><i className="legend-input" />输入<i className="legend-output" />输出</div>
        </div>
        <svg viewBox="0 0 260 126" preserveAspectRatio="none" role="img" aria-label="每次模型调用累计输入输出 token 折线图">
          <defs>
            <linearGradient id="output-area" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#6f5cff" stopOpacity=".24" />
              <stop offset="1" stopColor="#6f5cff" stopOpacity="0" />
            </linearGradient>
          </defs>
          <path className="chart-grid" d="M0 14H260M0 63H260M0 112H260" />
          <polygon className="chart-area" points={`0,119 ${points(outputs, 260, 112, tokenScaleMax)} 260,119`} />
          <polyline className="chart-line chart-line--input" points={points(inputs, 260, 112, tokenScaleMax)} />
          <polyline className="chart-line chart-line--output" points={points(outputs, 260, 112, tokenScaleMax)} />
        </svg>
        <div className="chart-axis">
          {visibleData.map((_, index) => <span key={index}>{firstVisibleCall + index}</span>)}
        </div>
      </section>

      <section className="runtime-stats" aria-label="运行统计">
        <div className="insight-section-heading"><div><strong>运行数据</strong><small>当前会话</small></div></div>
        <dl>
          <div><dt><BranchesOutlined />子智能体</dt><dd>{agents}</dd></div>
          <div><dt><ToolOutlined />工具调用</dt><dd>{tools}</dd></div>
          <div><dt><span className="stat-ring" style={{ "--value": `${success * 3.6}deg` } as CSSProperties} />完成率</dt><dd>{success}%</dd></div>
        </dl>
      </section>

      <footer className="insights-footer">
        <span>KG RUN STATUS</span>
        <strong><i />{isRunning ? "知识图谱正在构建" : "等待下一项任务"}</strong>
      </footer>
    </aside>
  );
}, sameInsightInputs);
