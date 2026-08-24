import { ArrowDownOutlined, ArrowUpOutlined, BranchesOutlined, ToolOutlined } from "@ant-design/icons";
import type { CSSProperties } from "react";
import type { ChatTurn } from "./ConversationThread";

interface SessionInsightsProps {
  turns: ChatTurn[];
  isRunning: boolean;
}

interface TokenPoint {
  input: number;
  output: number;
  actual: boolean;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function findUsage(value: unknown, depth = 0): { input: number; output: number } | null {
  if (!value || typeof value !== "object" || depth > 4) return null;
  const record = value as Record<string, unknown>;
  const input = finiteNumber(record.input_tokens) ?? finiteNumber(record.prompt_tokens);
  const output = finiteNumber(record.output_tokens) ?? finiteNumber(record.completion_tokens);
  if (input !== null || output !== null) {
    return { input: input ?? 0, output: output ?? 0 };
  }
  for (const nested of Object.values(record)) {
    const usage = findUsage(nested, depth + 1);
    if (usage) return usage;
  }
  return null;
}

function estimateTokens(content: string): number {
  if (!content) return 0;
  const han = (content.match(/[\u3400-\u9fff]/g) || []).length;
  const rest = Math.max(0, content.length - han);
  return Math.max(1, Math.round(han * 1.05 + rest / 4));
}

function tokenPoint(turn: ChatTurn): TokenPoint {
  let input = 0;
  let output = 0;
  let actual = false;
  for (const event of turn.events) {
    const usage = findUsage(event.data);
    if (usage) {
      input += usage.input;
      output += usage.output;
      actual = true;
    }
  }
  if (!actual) {
    input = estimateTokens(turn.content);
    output = estimateTokens(turn.result);
  }
  return { input, output, actual };
}

function compact(value: number): string {
  if (value >= 1000) return `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}k`;
  return String(value);
}

function points(values: number[], width: number, height: number): string {
  const max = Math.max(...values, 1);
  const step = values.length > 1 ? width / (values.length - 1) : 0;
  return values.map((value, index) => `${index * step},${height - (value / max) * (height - 14) - 7}`).join(" ");
}

export function SessionInsights({ turns, isRunning }: SessionInsightsProps) {
  const data = turns.map(tokenPoint).slice(-8);
  const chartData = data.length > 0 ? data : [{ input: 0, output: 0, actual: false }];
  const inputs = chartData.map((item) => item.input);
  const outputs = chartData.map((item) => item.output);
  const totalInput = data.reduce((sum, item) => sum + item.input, 0);
  const totalOutput = data.reduce((sum, item) => sum + item.output, 0);
  const tools = turns.reduce((sum, turn) => sum + turn.events.filter((event) => event.event === "tool_start").length, 0);
  const agents = turns.reduce((sum, turn) => sum + turn.events.filter((event) => event.event === "assistant_call").length, 0);
  const hasActual = data.some((item) => item.actual);
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
          <em>{hasActual ? "API" : "近似估算"}</em>
        </div>
        <strong>{compact(totalInput + totalOutput)}</strong>
        <small>当前会话累计</small>
        <div className="token-split">
          <span><ArrowDownOutlined />输入 <b>{compact(totalInput)}</b></span>
          <span><ArrowUpOutlined />输出 <b>{compact(totalOutput)}</b></span>
        </div>
      </section>

      <section className="token-chart" aria-label="每轮 Token 变化">
        <div className="insight-section-heading">
          <div><strong>Token 变化</strong><small>最近 {Math.max(data.length, 1)} 轮对话</small></div>
          <div className="chart-legend"><i className="legend-input" />输入<i className="legend-output" />输出</div>
        </div>
        <svg viewBox="0 0 260 126" preserveAspectRatio="none" role="img" aria-label="输入输出 token 折线图">
          <defs>
            <linearGradient id="output-area" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#6f5cff" stopOpacity=".24" />
              <stop offset="1" stopColor="#6f5cff" stopOpacity="0" />
            </linearGradient>
          </defs>
          <path className="chart-grid" d="M0 14H260M0 63H260M0 112H260" />
          <polygon className="chart-area" points={`0,119 ${points(outputs, 260, 112)} 260,119`} />
          <polyline className="chart-line chart-line--input" points={points(inputs, 260, 112)} />
          <polyline className="chart-line chart-line--output" points={points(outputs, 260, 112)} />
        </svg>
        <div className="chart-axis">
          {chartData.map((_, index) => <span key={index}>{index + 1}</span>)}
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
}
