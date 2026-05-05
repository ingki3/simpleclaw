"use client";

/**
 * Topbar — Admin 2.0 App Shell 상단 바 (BIZ-113).
 *
 * admin.pen reusable `m4kJJP` (Component/Topbar) 박제:
 *  - left:   breadcrumb (영역명 + 부영역) — pathname → AREAS SSOT 매핑.
 *  - center: ⌘K Command Palette 트리거 버튼 (280px). 클릭/단축키 모두 onOpenPalette.
 *  - right:  agent status pill + theme toggle + (옵션) actor.
 *
 * 다크모드 토글은 ThemeProvider 의 setMode 를 직접 호출 — system/light/dark 3-way.
 * 변경은 localStorage 에 영속(STORAGE_KEY = "simpleclaw.admin2.theme").
 */

import { usePathname } from "next/navigation";
import { useTheme, type ThemeMode } from "@/design/ThemeProvider";
import { StatusPill, type StatusTone } from "@/design/atoms/StatusPill";
import { findAreaByPath } from "@/app/areas";
import { cn } from "@/lib/cn";

interface TopbarProps {
  /** ⌘K 트리거 버튼 / 단축키가 호출. shell layout 이 들고 있는 콜백. */
  onOpenPalette: () => void;
  /** Agent 데몬 상태 — Sidebar footer 와 동일한 출처 의도. */
  agentStatus?: { tone: StatusTone; label: string };
  /** 운영자 표시 (옵션). null 이면 actor 영역 숨김. */
  actor?: string | null;
}

const THEME_LABEL: Record<ThemeMode, string> = {
  light: "라이트",
  dark: "다크",
  system: "시스템",
};

export function Topbar({
  onOpenPalette,
  agentStatus = { tone: "neutral", label: "데몬 상태 확인 중" },
  actor = null,
}: TopbarProps) {
  const pathname = usePathname() ?? "/";
  const area = findAreaByPath(pathname);
  const { mode, setMode } = useTheme();

  // 3-way 토글 순환: system → light → dark → system.
  const nextMode: ThemeMode =
    mode === "system" ? "light" : mode === "light" ? "dark" : "system";

  return (
    <header
      data-testid="topbar"
      role="banner"
      className="sticky top-0 z-30 flex h-14 items-center justify-between gap-4 border-b border-(--topbar-border) bg-(--topbar-bg) px-6"
    >
      {/* ─── Left: breadcrumb ──────────────────────────────── */}
      <nav
        aria-label="현재 위치"
        data-testid="topbar-breadcrumb"
        className="flex min-w-0 items-center gap-2 text-sm"
      >
        <span className="font-mono text-xs text-(--muted-foreground)">
          admin
        </span>
        <span className="text-(--muted-foreground)">/</span>
        <span className="truncate font-medium text-(--foreground-strong)">
          {area?.label ?? "—"}
        </span>
        {area ? (
          <span className="hidden truncate text-xs text-(--muted-foreground) md:inline">
            · {area.description}
          </span>
        ) : null}
      </nav>

      {/* ─── Center: ⌘K trigger ───────────────────────────── */}
      <button
        type="button"
        onClick={onOpenPalette}
        data-testid="topbar-palette-trigger"
        aria-label="명령어 열기 (⌘K)"
        className={cn(
          "hidden h-9 w-72 items-center gap-2 rounded-(--radius-m) border border-(--border) bg-(--surface) px-3 text-left text-sm text-(--muted-foreground) transition-colors hover:border-(--border-strong) md:inline-flex",
        )}
      >
        <span aria-hidden>⌘</span>
        <span className="flex-1 truncate">영역·설정 검색…</span>
        <kbd className="rounded-(--radius-sm) border border-(--border) bg-(--card) px-1.5 py-0.5 font-mono text-[11px] text-(--foreground)">
          ⌘K
        </kbd>
      </button>

      {/* ─── Right: agent status / theme / actor ──────────── */}
      <div className="flex items-center gap-3">
        <StatusPill tone={agentStatus.tone}>{agentStatus.label}</StatusPill>

        <div
          role="radiogroup"
          aria-label="테마"
          data-testid="theme-toggle"
          className="inline-flex items-center rounded-(--radius-m) border border-(--border) bg-(--card) p-0.5"
        >
          {(["light", "dark", "system"] as const).map((m) => {
            const active = mode === m;
            return (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => setMode(m)}
                data-testid={`theme-toggle-${m}`}
                className={cn(
                  "rounded-(--radius-sm) px-2 py-1 text-xs",
                  active
                    ? "bg-(--primary) text-(--primary-foreground)"
                    : "text-(--muted-foreground) hover:text-(--foreground)",
                )}
              >
                {THEME_LABEL[m]}
              </button>
            );
          })}
        </div>

        {/* 키보드 단축키 한 번에 다음 모드로 — Topbar 와 무관하게 onClick 으로 노출. */}
        <button
          type="button"
          onClick={() => setMode(nextMode)}
          aria-label={`다음 테마로: ${THEME_LABEL[nextMode]}`}
          data-testid="theme-cycle"
          className="sr-only"
        >
          cycle theme
        </button>

        {actor ? (
          <span
            data-testid="topbar-actor"
            className="hidden font-mono text-xs text-(--muted-foreground) md:inline"
          >
            {actor}
          </span>
        ) : null}
      </div>
    </header>
  );
}
