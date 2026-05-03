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
    <div className="flex min-h-screen bg-[--background] text-[--foreground]">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar />
        <main className="flex-1 overflow-y-auto p-8" id="main-content">
          {children}
        </main>
      </div>
    </div>
  );
}
