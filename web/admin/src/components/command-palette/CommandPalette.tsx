"use client";

/**
 * Command Palette — DESIGN.md §4.8.
 *
 * 1차 결과: 화면 이동(11개 영역). 2차: 설정 키 이름. 시크릿은 *이름만*.
 * 본 1차 스캐폴딩에서는 화면 이동까지만 동작하며, 설정 키 인덱스는 후속 이슈에서 주입한다.
 *
 * 구현 노트:
 *  - role="dialog" + aria-modal로 modal-like 시맨틱.
 *  - Escape / 바깥 클릭으로 닫기. 진입 시 input에 자동 포커스.
 *  - 단축키(⌘K) 자체는 Topbar에서 토글한다.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Search } from "lucide-react";
import { NAV_ITEMS } from "@/lib/nav";
import { getIcon } from "@/lib/icon";
import { cn } from "@/lib/cn";

export interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);

  // 진입 시 input 포커스 + 닫힘 시 query 초기화.
  useEffect(() => {
    if (open) {
      setQuery("");
      // 다음 틱에 포커스 — focus trap은 후속 이슈에서 보강.
      const t = window.setTimeout(() => inputRef.current?.focus(), 0);
      return () => window.clearTimeout(t);
    }
  }, [open]);

  // Escape로 닫기.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        onOpenChange(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return NAV_ITEMS;
    return NAV_ITEMS.filter(
      (n) =>
        n.label.toLowerCase().includes(q) ||
        n.href.toLowerCase().includes(q) ||
        n.description.toLowerCase().includes(q),
    );
  }, [query]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="명령 팔레트"
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 px-4 pt-[12vh]"
      onClick={() => onOpenChange(false)}
    >
      <div
        // 내부 클릭은 모달 유지를 위해 propagate 방지.
        onClick={(e) => e.stopPropagation()}
        className="flex w-full max-w-xl flex-col overflow-hidden rounded-[--radius-l] border border-[--border] bg-[--card-elevated] shadow-[--shadow-l]"
      >
        <div className="flex items-center gap-3 border-b border-[--border] px-4 py-3">
          <Search
            size={16}
            aria-hidden
            className="text-[--muted-foreground]"
          />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="화면, 설정 키, 시크릿 이름…"
            className="flex-1 bg-transparent text-sm text-[--foreground] placeholder:text-[--placeholder] outline-none"
            aria-label="명령 검색"
          />
          <kbd className="rounded-[--radius-sm] border border-[--border] bg-[--background] px-1.5 py-0.5 font-mono text-[10px] text-[--muted-foreground]">
            Esc
          </kbd>
        </div>
        <ul className="max-h-[50vh] overflow-y-auto py-1">
          {results.length === 0 ? (
            <li className="px-4 py-8 text-center text-sm text-[--muted-foreground]">
              결과가 없어요.
            </li>
          ) : (
            results.map((item) => {
              const Icon = getIcon(item.icon);
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    onClick={() => onOpenChange(false)}
                    className={cn(
                      "flex items-center gap-3 px-4 py-2.5 text-sm text-[--foreground] transition-colors hover:bg-[--surface]",
                    )}
                  >
                    <Icon
                      size={16}
                      aria-hidden
                      className="text-[--muted-foreground]"
                    />
                    <span>{item.label}</span>
                    <span className="ml-auto text-xs text-[--muted-foreground]">
                      {item.href}
                    </span>
                  </Link>
                </li>
              );
            })
          )}
        </ul>
      </div>
    </div>
  );
}
