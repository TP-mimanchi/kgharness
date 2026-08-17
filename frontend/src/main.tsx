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
          colorPrimary: "#e85639",
          colorSuccess: "#93a67a",
          colorWarning: "#d3a56c",
          colorError: "#e85639",
          colorInfo: "#e85639",
          colorBgBase: "#11100f",
          colorBgContainer: "#1a1816",
          colorTextBase: "#f4eee0",
          colorBorder: "rgba(244, 238, 224, 0.14)",
          borderRadius: 4,
          fontFamily:
            "'IBM Plex Sans', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif",
          fontFamilyCode:
            "'JetBrains Mono', 'SFMono-Regular', Consolas, 'Liberation Mono', monospace"
        },
        components: {
          Button: {
            controlHeightLG: 46,
            primaryShadow: "0 10px 30px rgba(232, 86, 57, 0.2)"
          },
          Input: {
            activeBorderColor: "#e85639",
            hoverBorderColor: "#e85639"
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
