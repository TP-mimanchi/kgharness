import "antd/dist/reset.css";
import { App as AntApp, ConfigProvider } from "antd";
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider
      theme={{
        token: {
          colorPrimary: "#201d26",
          colorSuccess: "#6b8e57",
          colorWarning: "#b8793e",
          colorError: "#b54d62",
          colorInfo: "#8d72d8",
          colorBgBase: "#f7f6f2",
          colorBgContainer: "rgba(255, 255, 255, 0.9)",
          colorBorder: "#dedbe4",
          borderRadius: 14,
          fontFamily:
            "'IBM Plex Sans', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif",
          fontFamilyCode:
            "'JetBrains Mono', 'SFMono-Regular', Consolas, 'Liberation Mono', monospace"
        },
        components: {
          Button: {
            controlHeightLG: 46,
            primaryShadow: "0 10px 24px rgba(32, 29, 38, 0.16)"
          },
          Input: {
            activeBorderColor: "#8d72d8",
            hoverBorderColor: "#8d72d8"
          }
        }
      }}
    >
      <AntApp>
        <App />
      </AntApp>
    </ConfigProvider>
  </React.StrictMode>
);
