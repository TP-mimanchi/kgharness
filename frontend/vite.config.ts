import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) {
            return undefined;
          }
          if (id.includes("/react/") || id.includes("/react-dom/") || id.includes("/scheduler/")) {
            return "react-vendor";
          }
          if (
            id.includes("/antd/") ||
            id.includes("/@ant-design/") ||
            id.includes("/rc-") ||
            id.includes("/@rc-component/")
          ) {
            return "antd-vendor";
          }
          return "vendor";
        }
      }
    }
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true
      },
      "/ws": {
        target: "ws://localhost:8000",
        ws: true
      }
    }
  }
});
