/**
 * Vitest 설정 — Admin 2.0 단위 테스트 (S0 스캐폴드).
 *
 * - jsdom 환경: React 컴포넌트 렌더 검증.
 * - 경로 alias: tsconfig 의 `@/*` 와 일치.
 * - Playwright e2e 는 별도 (`playwright.config.ts`) — vitest 가 e2e 폴더를 잡지 않도록 exclude.
 */
import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}", "tests/unit/**/*.test.{ts,tsx}"],
    exclude: ["tests/e2e/**", "node_modules/**", ".next/**"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
});
