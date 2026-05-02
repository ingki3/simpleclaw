/**
 * RootLayout — App Router 진입점.
 *
 * ThemeProvider로 전체를 감싸 라이트/다크 토큰 swap을 지원하고,
 * Shell이 Sidebar(240) + Topbar + Main의 시각 구조를 정의한다.
 *
 * <html>의 초기 클래스는 비어 있으며(시스템 모드), 클라이언트 hydrate 시점에
 * theme.tsx의 useEffect가 저장값을 적용한다. SSR 결과의 색은 prefers-color-scheme로
 * 결정되므로 light/dark 사이의 깜빡임은 발생하지 않는다.
 */

import type { Metadata } from "next";
import { ThemeProvider } from "@/lib/theme";
import { Shell } from "@/components/layout/Shell";
import { ToastProvider } from "@/components/primitives";
import "./globals.css";

export const metadata: Metadata = {
  title: "SimpleClaw Admin",
  description: "단일 운영자용 SimpleClaw 데몬 설정 화면",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ko" suppressHydrationWarning>
      <body>
        <ThemeProvider>
          <ToastProvider>
            <Shell>{children}</Shell>
          </ToastProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
