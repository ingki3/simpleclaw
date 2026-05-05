"use client";

/**
 * Shell — Admin 2.0 App Shell 합성 (BIZ-113).
 *
 * Sidebar + Topbar + CommandPalette + main slot 을 하나로 묶는다.
 * Next App Router 의 (shell)/layout 이 본 컴포넌트만 렌더하고, 11개 영역 페이지는
 * children 으로 들어온다 — 영역 sub-issue (S3~S13) 가 콘텐츠를 채울 때 본 파일은
 * *손대지 않는다*.
 *
 * 라우터 가드(인증 미완·권한 없음) — 본 단계는 단일 운영자 가정이라 항상 통과시키되,
 * `agentStatus` prop 이 "offline" 이면 main 영역에 빈 셸 + StatusPill 만 노출.
 */

import { useState, type ReactNode } from "react";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { CommandPalette, useCommandPaletteHotkey } from "./CommandPalette";
import { StatusPill, type StatusTone } from "@/design/atoms/StatusPill";

interface ShellProps {
  children: ReactNode;
}

export function Shell({ children }: ShellProps) {
  const [paletteOpen, setPaletteOpen] = useState(false);

  // ⌘K / Ctrl+K 글로벌 핫키 — 입력 중에도 동작.
  useCommandPaletteHotkey(() => setPaletteOpen(true));

  // S2 단계는 인증/권한 흐름이 없으므로 항상 정상 셸. agentStatus 는 후속 단계
  // (S3 Dashboard 또는 S13 System) 에서 실제 데몬 헬스 워치로 교체.
  const agentStatus: { tone: StatusTone; label: string } = {
    tone: "neutral",
    label: "데몬 상태 대기",
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-(--background) text-(--foreground)">
      <Sidebar daemonStatus="unknown" daemonVersion={null} />

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Skip link — DESIGN.md §10.2 키보드 시나리오 "본문 건너뛰기". */}
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-(--radius-m) focus:bg-(--card-elevated) focus:px-3 focus:py-1.5 focus:text-sm focus:text-(--foreground-strong) focus:shadow-[var(--shadow-m)]"
        >
          본문으로 건너뛰기
        </a>

        <Topbar
          onOpenPalette={() => setPaletteOpen(true)}
          agentStatus={agentStatus}
        />

        <main
          id="main-content"
          tabIndex={-1}
          data-testid="shell-main"
          className="flex-1 overflow-y-auto bg-(--background)"
        >
          {children}
        </main>
      </div>

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
      />

      {/* 가시 안내 — agentStatus 가 offline 이면 별도의 가드 토스트.
          현재는 단일 운영자라 noop 이지만 후속 단계에서 hook 으로 확장. */}
      {agentStatus.tone === "error" ? (
        <div
          role="status"
          aria-live="polite"
          data-testid="shell-status-banner"
          className="pointer-events-none fixed bottom-4 right-4 z-40"
        >
          <StatusPill tone="error">{agentStatus.label}</StatusPill>
        </div>
      ) : null}
    </div>
  );
}
