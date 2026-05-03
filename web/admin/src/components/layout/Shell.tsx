"use client";

/**
 * 앱 셸 — Sidebar(240) + (Topbar + Main) 의 2-zone 구조.
 *
 * DESIGN.md §3.3 Layout / §2.5 페이지 컨텐츠 padding 32.
 * RootLayout이 ThemeProvider를 감싸고, Shell은 시각 구조만 담당한다.
 */

import type { ReactNode } from "react";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";

export function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen bg-(--background) text-(--foreground)">
      {/* 키보드/스크린리더 사용자가 사이드바·헤더를 건너뛰고 본문으로 즉시 이동.
          평소엔 sr-only로 가려져 있고, 포커스를 받으면 좌상단에 시각적으로 노출된다. */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[60] focus:inline-flex focus:items-center focus:rounded-(--radius-m) focus:border focus:border-(--primary) focus:bg-(--card-elevated) focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:text-(--foreground-strong) focus:shadow-(--shadow-l)"
      >
        본문으로 건너뛰기
      </a>
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar />
        <main
          id="main-content"
          tabIndex={-1}
          className="flex-1 overflow-y-auto p-8 outline-none"
        >
          {children}
        </main>
      </div>
    </div>
  );
}
