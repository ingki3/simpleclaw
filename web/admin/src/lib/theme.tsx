"use client";

/**
 * 테마(라이트/다크) 컨텍스트.
 *
 * DESIGN.md §2.8: 시스템 prefers-color-scheme이 기본이고, 운영자가 수동 override하면
 * 그 선택이 localStorage에 영속된다. 컴포넌트는 토큰만 참조하므로 분기 코드는 0줄이다.
 *
 * 구현 노트:
 *  - SSR-safe: 초기 렌더에서는 "system"으로 고정하고, 클라이언트 hydrate 시점에 저장값을 읽는다.
 *  - <html> 태그의 클래스(`theme-light` | `theme-dark`)만 토글한다. system이면 클래스를 제거해
 *    CSS의 prefers-color-scheme 미디어 쿼리가 자연스럽게 적용되도록 한다.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type ThemeMode = "light" | "dark" | "system";

type ThemeContextValue = {
  /** 운영자가 명시한 모드 (저장값). */
  mode: ThemeMode;
  /** 실제 적용 중인 모드 — 시스템 모드면 미디어 쿼리 결과로 해석된 값. */
  resolved: "light" | "dark";
  setMode: (next: ThemeMode) => void;
};

const STORAGE_KEY = "simpleclaw.admin.theme";
const ThemeContext = createContext<ThemeContextValue | null>(null);

/**
 * <html>에 적용되어야 할 클래스명을 계산한다.
 * system이면 빈 문자열을 반환해 prefers-color-scheme 자동 분기에 맡긴다.
 */
function classForMode(mode: ThemeMode): string {
  if (mode === "light") return "theme-light";
  if (mode === "dark") return "theme-dark";
  return "";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>("system");
  const [systemDark, setSystemDark] = useState(false);

  // 클라이언트 hydrate 직후 저장된 모드를 적용한다.
  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY) as ThemeMode | null;
    if (stored === "light" || stored === "dark" || stored === "system") {
      setModeState(stored);
    }
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    setSystemDark(mq.matches);
    const handler = (e: MediaQueryListEvent) => setSystemDark(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  // <html> 클래스 동기화 — 컴포넌트 분기 0줄 규약을 지키기 위해 여기서만 토글한다.
  useEffect(() => {
    const root = document.documentElement;
    root.classList.remove("theme-light", "theme-dark");
    const next = classForMode(mode);
    if (next) root.classList.add(next);
  }, [mode]);

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next);
    window.localStorage.setItem(STORAGE_KEY, next);
  }, []);

  const value = useMemo<ThemeContextValue>(() => {
    const resolved: "light" | "dark" =
      mode === "system" ? (systemDark ? "dark" : "light") : mode;
    return { mode, resolved, setMode };
  }, [mode, systemDark, setMode]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used inside <ThemeProvider>");
  return ctx;
}
