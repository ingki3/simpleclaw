"use client";

/**
 * 상단 바 — DESIGN.md §3.3 / §4.5 Health Surfacing.
 *
 * 좌측: 페이지 타이틀 (현재 nav 항목으로부터 유도)
 * 우측: 헬스 dot 4종 + ⌘K 트리거 + 환경 pill + 다크모드 토글 + actor pill
 *
 * 1차 스캐폴딩이므로 헬스 값은 placeholder("green")이며, ⌘K 트리거는
 * 페이지 어디서나 단축키로 열리는 CommandPalette를 setOpen으로 호출한다.
 */

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Search, Sun, Moon, Monitor } from "lucide-react";
import { NAV_ITEMS } from "@/lib/nav";
import { useTheme, type ThemeMode } from "@/lib/theme";
import { cn } from "@/lib/cn";
import { CommandPalette } from "@/components/command-palette/CommandPalette";

/**
 * 4가지 핵심 헬스 영역 — DESIGN.md §4.5의 TopBar 우측 dots 예시.
 * 후속 이슈에서 daemon API 연동 시 각 항목의 status를 실시간 갱신한다.
 */
const HEALTH_DOTS = [
  { key: "daemon", label: "데몬", status: "ok" as const },
  { key: "webhook", label: "웹훅", status: "ok" as const },
  { key: "llm", label: "LLM", status: "ok" as const },
  { key: "cron", label: "Cron", status: "ok" as const },
];

const STATUS_CLASS: Record<"ok" | "warn" | "error" | "unknown", string> = {
  ok: "bg-[--color-success]",
  warn: "bg-[--color-warning]",
  error: "bg-[--color-error]",
  unknown: "bg-[--muted-foreground]",
};

export function Topbar() {
  const pathname = usePathname();
  const current = NAV_ITEMS.find(
    (n) => pathname === n.href || pathname?.startsWith(n.href + "/"),
  );
  const [paletteOpen, setPaletteOpen] = useState(false);

  // ⌘K / Ctrl+K 단축키 — 입력 포커스 안에서도 동작하도록 keydown을 window에 건다.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <header className="flex h-16 items-center gap-4 border-b border-[--border] bg-[--background] px-8">
      {/* 좌: 페이지 타이틀 */}
      <h1 className="text-xl font-semibold text-[--foreground-strong]">
        {current?.label ?? "SimpleClaw"}
      </h1>

      {/* 우 영역 spacer */}
      <div className="ml-auto flex items-center gap-3">
        {/* 헬스 dots — 색만으로 상태를 표현하지 않도록 title(라벨) 동반 */}
        <ul className="flex items-center gap-2" aria-label="시스템 상태">
          {HEALTH_DOTS.map((d) => (
            <li
              key={d.key}
              className="flex items-center gap-1.5 rounded-[--radius-pill] bg-[--card] px-2 py-1 text-xs text-[--muted-foreground]"
              title={`${d.label}: 정상`}
            >
              <span
                aria-hidden
                className={cn(
                  "inline-block h-1.5 w-1.5 rounded-[--radius-pill]",
                  STATUS_CLASS[d.status],
                )}
              />
              <span>{d.label}</span>
            </li>
          ))}
        </ul>

        {/* ⌘K 트리거 */}
        <button
          type="button"
          onClick={() => setPaletteOpen(true)}
          className="flex items-center gap-2 rounded-[--radius-m] border border-[--border] bg-[--card] px-3 py-1.5 text-xs text-[--muted-foreground] transition-colors hover:bg-[--surface]"
          aria-label="명령 팔레트 열기"
        >
          <Search size={14} aria-hidden />
          <span>검색·이동</span>
          <kbd className="rounded-[--radius-sm] border border-[--border] bg-[--background] px-1.5 py-0.5 font-mono text-[10px]">
            ⌘K
          </kbd>
        </button>

        {/* 환경 pill — 1차 스캐폴딩이므로 local 고정 */}
        <span className="rounded-[--radius-pill] border border-[--border] bg-[--card] px-2.5 py-1 font-mono text-[10px] uppercase tracking-wide text-[--muted-foreground]">
          local
        </span>

        <ThemeToggle />

        {/* Actor pill — 단일 운영자 가정 */}
        <span className="flex items-center gap-2 rounded-[--radius-pill] border border-[--border] bg-[--card] px-2.5 py-1 text-xs text-[--foreground]">
          <span
            aria-hidden
            className="grid h-5 w-5 place-items-center rounded-[--radius-pill] bg-[--primary] text-[10px] font-semibold text-[--primary-foreground]"
          >
            S
          </span>
          <span>operator</span>
        </span>
      </div>

      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </header>
  );
}

/**
 * 테마 토글 — light → dark → system 3-state 사이클.
 * DESIGN.md §2.8: 시스템 prefer-color-scheme이 기본이므로 system도 명시적으로 노출한다.
 */
function ThemeToggle() {
  const { mode, setMode } = useTheme();

  const next: Record<ThemeMode, ThemeMode> = {
    light: "dark",
    dark: "system",
    system: "light",
  };
  const Icon = mode === "light" ? Sun : mode === "dark" ? Moon : Monitor;
  const label =
    mode === "light" ? "라이트" : mode === "dark" ? "다크" : "시스템";

  return (
    <button
      type="button"
      onClick={() => setMode(next[mode])}
      aria-label={`테마: ${label} (클릭으로 전환)`}
      title={`테마: ${label}`}
      className="grid h-8 w-8 place-items-center rounded-[--radius-m] border border-[--border] bg-[--card] text-[--foreground] transition-colors hover:bg-[--surface]"
    >
      <Icon size={16} aria-hidden />
    </button>
  );
}
