import { CheckCircleOutlined, CopyOutlined } from "@ant-design/icons";
import { Button, Empty, Tooltip } from "antd";

interface ResultPanelProps {
  result: string;
  onCopy: () => void;
}

export function ResultPanel({ result, onCopy }: ResultPanelProps) {
  return (
    <section className="console-panel result-panel" aria-labelledby="result-title">
      <div className="panel-heading">
        <div>
          <span className="panel-kicker">FINAL ANSWER</span>
          <h2 id="result-title">最终回复</h2>
        </div>
        {result ? (
          <Tooltip title="复制最终回复">
            <Button
              aria-label="复制最终回复"
              className="icon-button"
              icon={<CopyOutlined />}
              onClick={onCopy}
              shape="circle"
            />
          </Tooltip>
        ) : (
          <CheckCircleOutlined className="panel-heading-icon" aria-hidden />
        )}
      </div>

      {result ? (
        <pre className="result-console">{result}</pre>
      ) : (
        <div className="compact-empty">
          <Empty description="任务完成后会在这里显示最终回答" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        </div>
      )}
    </section>
  );
}
