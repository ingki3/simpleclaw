/**
 * Vitest setup — Admin 2.0.
 *
 * 책임:
 *  1) testing-library 의 자동 cleanup 등록 — vitest globals 가 false 라
 *     `afterEach` 가 전역 노출되지 않으므로 본 파일에서 명시적으로 wiring.
 *  2) jsdom 미지원 API stub (matchMedia 등) — ThemeProvider 가 의존.
 */
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});

// jsdom 은 matchMedia 를 제공하지 않는다. ThemeProvider 의 prefers-color-scheme
// 분기가 해당 함수를 즉시 호출하므로, 항상 light(=matches:false) 로 응답하는
// stub 을 깔아둔다. 개별 테스트에서 dark 시뮬레이션이 필요하면 여기서 override.
if (typeof window !== "undefined" && !window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}
