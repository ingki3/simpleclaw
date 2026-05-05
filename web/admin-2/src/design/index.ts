/**
 * Admin 2.0 디자인 시스템 — 단일 진입점.
 *
 * S2 이후 모든 화면은 `@/design` 에서만 import 한다.
 * 토큰은 `tokens.ts` 에서, ThemeProvider 는 `ThemeProvider.tsx` 에서 직접 import.
 */

export * from "./atoms";
export * from "./molecules";
export * from "./domain";
export { ThemeProvider, useTheme } from "./ThemeProvider";
export type { ThemeMode, ResolvedTheme } from "./ThemeProvider";
