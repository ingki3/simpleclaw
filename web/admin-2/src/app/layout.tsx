/**
 * RootLayout — Admin 2.0 App Router 진입점.
 *
 * S1 단계: ThemeProvider 가 라이트/다크 모드를 <html data-theme> 으로 토글.
 * 토큰 본체는 tokens.css 에서 `[data-theme="dark"]` 셀렉터로 swap 된다.
 * S2 (App Shell) 에서 Sidebar/Topbar/Shell 합성이 더해진다.
 */
import type { Metadata } from "next";
import { ThemeProvider } from "@/design/ThemeProvider";
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
      <body>
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
