"use client";

/**
 * ThemeProvider — Admin 2.0 의 라이트/다크 모드 컨텍스트.
 *
 * 정책 (DESIGN.md §2.8):
 *  - 시스템 prefers-color-scheme 기본 + 운영자 수동 override.
 *  - 수동 모드는 localStorage 에 영속.
 *  - <html data-theme="light|dark"> 만 토글 — 컴포넌트 내부 분기는 0줄.
 *  - SSR-safe: 초기 렌더는 "system" 으로 고정, hydrate 후 저장값 복원.
 *
 * tokens.css 의 `[data-theme="dark"]` 셀렉터와 매칭된다.
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
export type ResolvedTheme = "light" | "dark";

interface ThemeContextValue {
  /** 운영자가 명시한 모드 — localStorage 와 동기. */
  mode: ThemeMode;
  /** 실제 적용 중인 모드 — system 이면 매체 쿼리 결과로 해석. */
  resolved: ResolvedTheme;
  setMode: (next: ThemeMode) => void;
}

const STORAGE_KEY = "simpleclaw.admin2.theme";
const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>("system");
  const [systemDark, setSystemDark] = useState(false);

  // hydrate 직후 저장값과 시스템 매체 쿼리 결과를 적용.
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

  // <html data-theme="..."> 동기화. system 이면 attribute 제거 → 매체 쿼리에 위임.
  useEffect(() => {
    const root = document.documentElement;
    if (mode === "system") {
      root.removeAttribute("data-theme");
    } else {
      root.setAttribute("data-theme", mode);
    }
  }, [mode]);

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* localStorage 사용 불가 환경(SSR/incognito)은 무시한다. */
    }
  }, []);

  const value = useMemo<ThemeContextValue>(() => {
    const resolved: ResolvedTheme =
      mode === "system" ? (systemDark ? "dark" : "light") : mode;
    return { mode, resolved, setMode };
  }, [mode, systemDark, setMode]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx)
    throw new Error("useTheme must be used inside <ThemeProvider>");
  return ctx;
}
