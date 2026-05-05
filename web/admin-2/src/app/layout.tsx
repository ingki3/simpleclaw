/**
 * RootLayout — Admin 2.0 App Router 진입점 (S0 스캐폴드).
 *
 * S0 단계에서는 ThemeProvider/Shell 등의 구조물 없이 최소 layout만 둔다.
 * - S1 (Design System): globals.css의 `@theme` 토큰과 ThemeProvider 추가.
 * - S2 (App Shell): Sidebar/Topbar/Shell 합성과 다크 모드 토글 wiring.
 */
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SimpleClaw Admin 2.0",
  description: "SimpleClaw 단일 운영자용 관리 화면 — Admin 2.0 (BIZ-108)",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ko" suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}
