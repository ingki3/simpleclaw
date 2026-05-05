/**
 * Playwright 설정 — Admin 2.0 e2e 스모크.
 *
 * S0 단계에서는 hello-world 페이지가 실제 브라우저에서 렌더되는지만 검증한다.
 * `webServer` 블록으로 next start 를 띄우므로 별도의 외부 데몬이 필요 없다.
 *
 * S1 이후 라우트가 늘어나면 projects 를 분리하고 a11y/visual 테스트를 추가한다.
 */
import { defineConfig, devices } from "@playwright/test";

const PORT = process.env.PORT && process.env.PORT.trim() !== "" ? process.env.PORT : "8089";
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: `node scripts/run-next.mjs start`,
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      PORT,
    },
  },
});
