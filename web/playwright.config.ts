import { defineConfig } from "@playwright/test";
const managed = !process.env.CONSOLE_BASE_URL;
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: process.env.CONSOLE_BASE_URL || "http://127.0.0.1:5173",
    channel:
      process.env.PLAYWRIGHT_CHANNEL ||
      (process.platform === "win32" ? "msedge" : undefined),
    headless: true,
    viewport: { width: 1440, height: 1080 },
    trace: "retain-on-failure",
  },
  reporter: "list",
  timeout: 60000,
  webServer: managed
    ? [
        {
          command:
            process.platform === "win32"
              ? "..\\.venv\\Scripts\\python.exe ..\\scripts\\serve_console_e2e.py"
              : "../.venv/bin/python ../scripts/serve_console_e2e.py",
          url: "http://127.0.0.1:18000/health",
          reuseExistingServer: false,
          timeout: 60000,
        },
        {
          command: "npm run dev -- --strictPort",
          url: "http://127.0.0.1:5173",
          env: { AGENTTEST_API_PROXY: "http://127.0.0.1:18000" },
          reuseExistingServer: false,
        },
      ]
    : undefined,
});
