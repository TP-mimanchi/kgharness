import { CloudServerOutlined, DatabaseOutlined, FileSearchOutlined } from "@ant-design/icons";

const agents = [
  {
    icon: <CloudServerOutlined aria-hidden />,
    name: "网络搜索助手",
    detail: "公开互联网检索，适合行业背景、公开趋势、外部资料"
  },
  {
    icon: <DatabaseOutlined aria-hidden />,
    name: "数据库查询助手",
    detail: "MySQL 表结构、商品信息、库存与销售数据查询"
  },
  {
    icon: <FileSearchOutlined aria-hidden />,
    name: "RAGFlow 助手",
    detail: "内部 PDF、白皮书、研报与私有知识库问答"
  }
];

export function AgentTopology() {
  return (
    <section className="console-panel topology-panel" aria-labelledby="topology-title">
      <div className="panel-heading">
        <div>
          <span className="panel-kicker">ROUTING MAP</span>
          <h2 id="topology-title">多智能体路由</h2>
        </div>
      </div>
      <div className="agent-hub">
        <div className="main-agent-node">
          <span>MAIN</span>
          <strong>调度主智能体</strong>
        </div>
        <div className="agent-links" aria-hidden>
          <span />
          <span />
          <span />
        </div>
        <div className="agent-node-list">
          {agents.map((agent) => (
            <div className="agent-node" key={agent.name}>
              <div className="agent-node-icon">{agent.icon}</div>
              <div>
                <strong>{agent.name}</strong>
                <p>{agent.detail}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
