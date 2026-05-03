"use client";

/**
 * 사이드바 — DESIGN.md §3.3 Layout / 폭 240px / 11개 영역.
 *
 * 구조:
 *   [로고] → [Nav 11] → [Footer: 데몬 상태 dot + 버전]
 *
 * 규약:
 *  - 활성 항목은 brand-tint 배경 + brand 텍스트로 강조.
 *  - hover/focus는 surface 변종으로 자연스럽게 분리, 포커스 링은 globals.css에서 일괄 처리.
 *  - Sidebar의 활성 상태는 usePathname 기반(루트 경로 prefix 매칭).
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { NAV_ITEMS } from "@/lib/nav";
import { getIcon } from "@/lib/icon";
import { cn } from "@/lib/cn";

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside
      aria-label="주요 영역"
      className="flex h-screen w-60 shrink-0 flex-col border-r border-[--border-divider] bg-[--surface]"
    >
      {/* 로고 영역 — 32px 가로 패딩으로 페이지 컨텐츠와 시각적으로 정렬 */}
      <div className="flex h-16 items-center gap-3 px-6">
        <div
          aria-hidden
          className="grid h-8 w-8 place-items-center rounded-[--radius-m] bg-[--primary] text-[--primary-foreground] font-semibold"
        >
          SC
        </div>
        <div className="flex flex-col leading-tight">
          <span className="text-[16px] font-semibold text-[--foreground-strong]">
            SimpleClaw
          </span>
          <span className="text-xs text-[--muted-foreground]">Admin</span>
        </div>
      </div>

      {/* 11개 nav — overflow는 세로로만 허용 */}
      <nav className="flex-1 overflow-y-auto px-3 py-2" aria-label="설정 영역">
        <ul className="flex flex-col gap-0.5">
          {NAV_ITEMS.map((item) => {
            const Icon = getIcon(item.icon);
            const active =
              pathname === item.href || pathname?.startsWith(item.href + "/");
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  title={item.description}
                  className={cn(
                    "flex items-center gap-3 rounded-[--radius-m] px-4 py-2.5 text-sm transition-colors",
                    active
                      ? "bg-[--primary-tint] text-[--primary] font-medium"
                      : "text-[--foreground] hover:bg-[--card]"
                  )}
                >
                  <Icon
                    size={18}
                    strokeWidth={active ? 2.25 : 1.75}
                    aria-hidden
                  />
                  <span>{item.label}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* Footer — 데몬 헬스 + 버전. 실제 값은 후속 이슈에서 API 연동. */}
      <div className="border-t border-[--border-divider] px-4 py-3">
        <div className="flex items-center gap-2 text-xs text-[--muted-foreground]">
          <span
            aria-hidden
            className="inline-block h-2 w-2 rounded-[--radius-pill] bg-[--color-success]"
          />
          <span>데몬 정상</span>
          <span className="ml-auto font-mono">v0.1.0</span>
        </div>
      </div>
    </aside>
  );
}
