import { defineConfig } from "@playwright/test";
const managed = !process.env.CONSOLE_BASE_URL;
const apiPort = Number(process.env.CONSOLE_API_PORT || 18000);
const webPort = Number(process.env.CONSOLE_WEB_PORT || 5173);
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: process.env.CONSOLE_BASE_URL || `http://127.0.0.1:${webPort}`,
    channel:
      process.env.PLAYWRIGHT_CHANNEL ||
      (process.platform === "win32" ? "msedge" : undefined),
    headless: true,
    viewport: { width: 1440, height: 1080 },
    trace: "off",
  },
  reporter: "list",
  timeout: 60000,
  expect: { timeout: 15000 },
  webServer: managed
    ? [
        {
          command:
            process.platform === "win32"
              ? `..\\.venv\\Scripts\\python.exe ..\\scripts\\serve_console_e2e.py --port ${apiPort}`
              : `../.venv/bin/python ../scripts/serve_console_e2e.py --port ${apiPort}`,
          url: `http://127.0.0.1:${apiPort}/health`,
          reuseExistingServer: false,
          timeout: 60000,
        },
        {
          command: `npm run dev -- --strictPort --port ${webPort}`,
          url: `http://127.0.0.1:${webPort}`,
          env: { AGENTTEST_API_PROXY: `http://127.0.0.1:${apiPort}` },
          reuseExistingServer: false,
        },
      ]
    : undefined,
});
