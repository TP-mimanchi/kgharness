export type AgentPhase = "listening" | "thinking" | "executing" | "complete";

const PHASES: Array<{
  id: AgentPhase;
  index: string;
  label: string;
  detail: string;
}> = [
  { id: "listening", index: "01", label: "聆听", detail: "捕捉任务意图" },
  { id: "thinking", index: "02", label: "思考", detail: "拆解问题路径" },
  { id: "executing", index: "03", label: "执行", detail: "调度工具与资料" },
  { id: "complete", index: "04", label: "完成", detail: "整理结果并交付" },
];

interface AgentStateRibbonProps {
  phase: AgentPhase;
}

export function AgentStateRibbon({ phase }: AgentStateRibbonProps) {
  const activeIndex = PHASES.findIndex((item) => item.id === phase);

  return (
    <section className={`agent-state-ribbon agent-state-ribbon--${phase}`} aria-label="Agent 实时状态">
      <div className="state-ribbon-heading">
        <span>AGENT PULSE</span>
        <strong>{phase === "complete" ? "已完成" : `${PHASES[activeIndex].label}中`}</strong>
      </div>
      <ol className="state-phases">
        {PHASES.map((item, index) => {
          const status = index === activeIndex ? "active" : index < activeIndex ? "passed" : "waiting";
          return (
            <li className={`state-phase state-phase--${status}`} key={item.id}>
              <span className="state-phase-orb" aria-hidden>
                <i />
              </span>
              <span className="state-phase-copy">
                <small>{item.index}</small>
                <strong>{item.label}</strong>
                <em>{item.detail}</em>
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
