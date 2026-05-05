/**
 * Admin 2.0 — 디자인 토큰 (TypeScript exports)
 *
 * 본 모듈은 tokens.css 의 CSS 변수와 1:1 매칭되는 TS 상수 SSOT 다.
 * 사용처:
 *  1) 런타임에 토큰 값을 코드에서 참조해야 하는 경우 (예: SVG fill, 차트 라이브러리).
 *     컴포넌트 스타일은 우선 CSS 변수(`var(--color-success)`)로 작성하고,
 *     토큰을 *값으로* 다뤄야 할 때만 본 모듈을 import 한다.
 *  2) 단위 테스트에서 토큰 매핑이 깨지지 않았는지 검증.
 *
 * 다크 모드 값은 `darkOverrides` 에 분리해 두며, 시각적 적용은 CSS 만으로 끝낸다.
 */

export const colors = {
  light: {
    neutral0: "#ffffff",
    neutral50: "#f7f8fa",
    neutral100: "#eef1f5",
    neutral200: "#e2e6ec",
    neutral300: "#cbd2da",
    neutral400: "#9aa3af",
    neutral500: "#6b7280",
    neutral600: "#4b5563",
    neutral700: "#374151",
    neutral800: "#1f2937",
    neutral900: "#0b0f14",
    brand50: "#eef0ff",
    brand500: "#5b6cf6",
    brand600: "#4453e0",
    success500: "#16a34a",
    success50: "#e7f8ee",
    warning500: "#d97706",
    warning50: "#fff4e5",
    danger500: "#dc2626",
    danger50: "#fdecec",
    info500: "#0284c7",
    info50: "#e5f4fb",
  },
  dark: {
    neutral0: "#0b0f14",
    neutral50: "#10151b",
    neutral100: "#161c24",
    neutral200: "#1f2731",
    neutral300: "#2a3441",
    neutral400: "#3d4a5c",
    neutral500: "#5a6779",
    neutral600: "#8a95a8",
    neutral700: "#b6bfce",
    neutral800: "#d6dce6",
    neutral900: "#f1f4f9",
    brand50: "#1a2244",
    brand500: "#7c8bff",
    brand600: "#5b6cf6",
    success500: "#22c55e",
    success50: "#0d2b19",
    warning500: "#f59e0b",
    warning50: "#2a1b05",
    danger500: "#ef4444",
    danger50: "#2a0f0f",
    info500: "#38bdf8",
    info50: "#06243a",
  },
} as const;

export const spacing = {
  s2: 2,
  s4: 4,
  s6: 6,
  s8: 8,
  s12: 12,
  s16: 16,
  s20: 20,
  s24: 24,
  s32: 32,
  s40: 40,
  s48: 48,
  s64: 64,
} as const;

export const radius = {
  none: 0,
  sm: 4,
  m: 8,
  l: 12,
  pill: 9999,
} as const;

export const shadow = {
  sm: "0 1px 2px rgba(11, 15, 20, 0.06)",
  m: "0 4px 16px rgba(11, 15, 20, 0.08)",
  l: "0 12px 32px rgba(11, 15, 20, 0.12)",
} as const;

export const motion = {
  fast: "120ms cubic-bezier(0.2, 0.8, 0.2, 1)",
  base: "180ms cubic-bezier(0.2, 0.8, 0.2, 1)",
  slow: "280ms cubic-bezier(0.2, 0.8, 0.2, 1)",
} as const;

export const typography = {
  fontPrimary:
    '"Inter", system-ui, -apple-system, "Segoe UI", sans-serif',
  fontMono:
    '"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace',
  scale: {
    "3xl": { size: 32, line: 40, weight: 600 },
    "2xl": { size: 24, line: 32, weight: 600 },
    xl: { size: 20, line: 28, weight: 600 },
    lg: { size: 18, line: 26, weight: 600 },
    md: { size: 16, line: 24, weight: 500 },
    base: { size: 14, line: 22, weight: 400 },
    sm: { size: 13, line: 20, weight: 400 },
    xs: { size: 12, line: 16, weight: 500 },
  },
} as const;

export type ThemeMode = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

/**
 * Semantic 토큰 이름 → CSS 변수 헬퍼.
 * 예: `cssVar("primary")` → `var(--primary)` — 인라인 style 에서 토큰 참조 시 사용.
 */
export function cssVar(name: string): string {
  return `var(--${name})`;
}
