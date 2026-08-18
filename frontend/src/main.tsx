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
          colorPrimary: "#007aff",
          colorSuccess: "#34c759",
          colorWarning: "#ff9500",
          colorError: "#ff3b30",
          colorInfo: "#007aff",
          colorBgBase: "#f2f2f7",
          colorBgContainer: "#ffffff",
          colorBgElevated: "#ffffff",
          colorTextBase: "#1c1c1e",
          colorBorder: "rgba(60, 60, 67, 0.14)",
          borderRadius: 10,
          boxShadowSecondary: "0 8px 32px rgba(0, 0, 0, 0.08)",
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", "PingFang SC", "Segoe UI", "Microsoft YaHei", "Helvetica Neue", sans-serif',
          fontFamilyCode:
            'ui-monospace, "SF Mono", "JetBrains Mono", "SFMono-Regular", Consolas, monospace'
        },
        components: {
          Button: {
            controlHeightLG: 46,
            primaryShadow: "0 4px 16px rgba(0, 122, 255, 0.28)"
          },
          Input: {
            activeBorderColor: "#007aff",
            hoverBorderColor: "#007aff"
          },
          Modal: {
            borderRadiusLG: 22
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
