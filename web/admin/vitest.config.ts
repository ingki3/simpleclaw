/**
 * Vitest 설정 — Admin UI 단위 테스트.
 *
 * - jsdom 환경: ``fetch``는 Node 기본 + ``msw/node``로 가로채기.
 * - 경로 alias: tsconfig의 ``@/*``와 일치.
 */
import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
});
