import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const proxy = {
    target: env.AGENTTEST_API_PROXY || "http://127.0.0.1:8000",
    changeOrigin: false,
  };
  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: true,
      proxy: { "/api": proxy, "/health": proxy },
    },
    preview: {
      port: 4173,
      strictPort: true,
      proxy: { "/api": proxy, "/health": proxy },
    },
  };
});
