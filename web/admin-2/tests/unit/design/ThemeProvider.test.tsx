/**
 * ThemeProvider 단위 테스트 — 모드 토글이 <html data-theme> 에 동기되는지 확인.
 *
 * jsdom 은 prefers-color-scheme 를 기본 false 로 응답하므로 system 모드는 light 로 해석된다.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act, render, renderHook } from "@testing-library/react";
import { ThemeProvider, useTheme } from "@/design/ThemeProvider";

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  // jsdom 의 matchMedia stub.
  Object.defineProperty(window, "matchMedia", {
    writable: true,
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
});

afterEach(() => {
  document.documentElement.removeAttribute("data-theme");
});

describe("ThemeProvider", () => {
  it("system 모드 시 data-theme attribute 가 제거된다", () => {
    render(
      <ThemeProvider>
        <div />
      </ThemeProvider>,
    );
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });

  it("setMode('dark') 시 <html data-theme=dark>", () => {
    const { result } = renderHook(() => useTheme(), {
      wrapper: ThemeProvider,
    });
    act(() => result.current.setMode("dark"));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(window.localStorage.getItem("simpleclaw.admin2.theme")).toBe("dark");
  });

  it("setMode('light') 시 <html data-theme=light>", () => {
    const { result } = renderHook(() => useTheme(), {
      wrapper: ThemeProvider,
    });
    act(() => result.current.setMode("light"));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it("system 으로 되돌리면 attribute 가 다시 제거된다", () => {
    const { result } = renderHook(() => useTheme(), {
      wrapper: ThemeProvider,
    });
    act(() => result.current.setMode("dark"));
    act(() => result.current.setMode("system"));
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });
});
