"use client";

/**
 * Sidebar — Admin 2.0 App Shell 좌측 내비게이션 (BIZ-113).
 *
 * admin.pen reusable `f8ZCrf` (Component/Sidebar) 박제:
 *  - brand 영역(상단) → 11개 영역 nav → spacer → footer(데몬 상태/버전).
 *  - active 항목 배경은 `--nav-active-bg` (BIZ-67 신규 토큰).
 *  - collapse 토글: 운영자 단일 사용이라 폭 토글만 제공 (240px ↔ 64px).
 *  - search 입력: 상단 brand 아래 1줄 — DESIGN.md §3.3 sidebar 항목 12 검색.
 *
 * 라우팅 매핑은 `src/app/areas.ts` 의 SSOT 만 참조한다 — 영역 추가/이름변경시
 * 본 파일은 손대지 않는다. (S3~S13 영역 sub-issue 들이 areas.ts 만 수정)
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useId, useMemo, useState } from "react";
import { cn } from "@/lib/cn";
import { AREAS, type AreaDef } from "@/app/areas";

interface SidebarProps {
  /** 데몬 상태 표시 — Topbar 와 footer 가 같은 값을 참조한다. */
  daemonStatus?: "online" | "degraded" | "offline" | "unknown";
  /** 데몬 버전 (예: v0.6.1). null 이면 footer 의 버전 영역 숨김. */
  daemonVersion?: string | null;
  /** Sidebar 가 collapsed 상태인지 (제어 모드). */
  collapsed?: boolean;
  /** collapse 토글 콜백. 외부에서 상태를 들고 싶을 때 사용. */
  onCollapseToggle?: () => void;
}

const STATUS_DOT: Record<NonNullable<SidebarProps["daemonStatus"]>, string> = {
  online: "bg-(--color-success)",
  degraded: "bg-(--color-warning)",
  offline: "bg-(--color-error)",
  unknown: "bg-(--muted-foreground)",
};

const STATUS_LABEL: Record<NonNullable<SidebarProps["daemonStatus"]>, string> = {
  online: "정상",
  degraded: "주의",
  offline: "중단",
  unknown: "알 수 없음",
};

export function Sidebar({
  daemonStatus = "unknown",
  daemonVersion = null,
  collapsed: controlledCollapsed,
  onCollapseToggle,
}: SidebarProps) {
  const pathname = usePathname() ?? "/";
  const [internalCollapsed, setInternalCollapsed] = useState(false);
  const collapsed = controlledCollapsed ?? internalCollapsed;
  const toggle = onCollapseToggle ?? (() => setInternalCollapsed((v) => !v));

  const [query, setQuery] = useState("");
  const searchId = useId();

  const filtered = useMemo<AreaDef[]>(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [...AREAS];
    return AREAS.filter(
      (a) =>
        a.label.toLowerCase().includes(q) ||
        a.path.toLowerCase().includes(q) ||
        a.keywords.some((k) => k.toLowerCase().includes(q)),
    );
  }, [query]);

  const isActive = (path: string) =>
    pathname === path || pathname.startsWith(`${path}/`);

  return (
    <aside
      data-testid="sidebar"
      data-collapsed={collapsed || undefined}
      aria-label="주요 영역"
      className={cn(
        "flex h-screen flex-col gap-1 border-r border-(--sidebar-border) bg-(--sidebar-bg) transition-[width]",
        collapsed ? "w-16 px-2 py-4" : "w-60 px-3 py-4",
      )}
    >
      {/* ─── Brand ──────────────────────────────────────────── */}
      <div className="flex items-center gap-2 px-2 pb-3">
        <span
          aria-hidden
          className="grid h-8 w-8 place-items-center rounded-(--radius-m) bg-(--primary) text-(--primary-foreground)"
        >
          SC
        </span>
        {collapsed ? null : (
          <span className="text-sm font-semibold text-(--foreground-strong)">
            SimpleClaw
          </span>
        )}
        <button
          type="button"
          onClick={toggle}
          aria-label={collapsed ? "사이드바 펼치기" : "사이드바 접기"}
          aria-pressed={collapsed}
          data-testid="sidebar-collapse"
          className="ml-auto inline-flex h-7 w-7 items-center justify-center rounded-(--radius-sm) text-(--muted-foreground) hover:bg-(--surface)"
        >
          {collapsed ? "›" : "‹"}
        </button>
      </div>

      {/* ─── Search ─────────────────────────────────────────── */}
      {collapsed ? null : (
        <div className="px-1 pb-2">
          <label htmlFor={searchId} className="sr-only">
            영역 검색
          </label>
          <input
            id={searchId}
            type="search"
            placeholder="영역 검색…"
            value={query}
            onChange={(e) => setQuery(e.currentTarget.value)}
            data-testid="sidebar-search"
            className="h-8 w-full rounded-(--radius-m) border border-(--border) bg-(--card) px-3 text-xs text-(--foreground) placeholder:text-(--placeholder) focus:border-(--primary) focus:outline-none"
          />
        </div>
      )}

      {/* ─── Nav ────────────────────────────────────────────── */}
      <nav aria-label="영역" className="flex flex-1 flex-col gap-0.5 overflow-y-auto">
        {filtered.map((area) => {
          const active = isActive(area.path);
          return (
            <Link
              key={area.path}
              href={area.path}
              data-testid={`sidebar-link-${area.path.slice(1)}`}
              data-active={active || undefined}
              aria-current={active ? "page" : undefined}
              title={collapsed ? area.label : undefined}
              className={cn(
                "flex items-center gap-2.5 rounded-(--radius-m) px-2.5 py-2 text-sm transition-colors",
                active
                  ? "bg-(--nav-active-bg) text-(--primary) font-medium"
                  : "text-(--foreground) hover:bg-(--surface)",
              )}
            >
              <span
                aria-hidden
                className="inline-grid h-5 w-5 place-items-center text-(--muted-foreground) data-[active=true]:text-(--primary)"
              >
                {area.icon}
              </span>
              {collapsed ? (
                <span className="sr-only">{area.label}</span>
              ) : (
                <span className="truncate">{area.label}</span>
              )}
            </Link>
          );
        })}
        {filtered.length === 0 ? (
          <p className="px-2 py-3 text-xs text-(--muted-foreground)">
            일치하는 영역이 없습니다.
          </p>
        ) : null}
      </nav>

      {/* ─── Footer (데몬 상태 / 버전) ─────────────────────── */}
      <div
        data-testid="sidebar-footer"
        className={cn(
          "mt-2 flex items-center gap-2 rounded-(--radius-m) bg-(--card) px-2 py-2 text-xs",
          collapsed && "justify-center",
        )}
      >
        <span
          aria-hidden
          className={cn(
            "inline-block h-2 w-2 rounded-(--radius-pill)",
            STATUS_DOT[daemonStatus],
          )}
        />
        {collapsed ? (
          <span className="sr-only">
            데몬: {STATUS_LABEL[daemonStatus]}
            {daemonVersion ? ` · ${daemonVersion}` : ""}
          </span>
        ) : (
          <>
            <span className="text-(--foreground)">
              데몬 {STATUS_LABEL[daemonStatus]}
            </span>
            {daemonVersion ? (
              <span className="ml-auto font-mono text-(--muted-foreground)">
                {daemonVersion}
              </span>
            ) : null}
          </>
        )}
      </div>
    </aside>
  );
}
