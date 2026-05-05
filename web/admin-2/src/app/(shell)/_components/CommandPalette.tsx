"use client";

/**
 * CommandPalette — ⌘K 오버레이 (BIZ-113 · DESIGN.md §3.3 / §4.8).
 *
 * 사양:
 *  - 진입: ⌘K (macOS) / Ctrl+K (Windows) — Topbar 트리거 버튼도 동일하게 사용.
 *  - 1차 결과: 영역 점프 (11개 — `src/app/areas.ts` SSOT).
 *  - 키보드 nav: ↑/↓ 항목 이동, Enter 선택, ESC 닫기.
 *  - 시크릿 키는 *이름만* 노출 — 본 단계에서는 영역 점프 결과만 다루고 시크릿 검색은
 *    S5(Skills/Recipes) 또는 S8(Secrets) sub-issue 가 확장한다.
 *
 * 시각: 카드 배경 `--card-elevated`, 셰도우 `--shadow-l`, 라운드 `--radius-l`.
 */

import { useRouter } from "next/navigation";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { cn } from "@/lib/cn";
import { searchAreas } from "@/app/areas";

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
}

export function CommandPalette({ open, onClose }: CommandPaletteProps) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);

  const items = useMemo(() => searchAreas(query), [query]);

  // 오픈 시 입력에 포커스, 인덱스 리셋. 닫힐 때 검색어 비움.
  useEffect(() => {
    if (open) {
      setActiveIndex(0);
      // 마이크로태스크 후 포커스 — Dialog 가 mount 된 다음 프레임에 입력 살아남.
      const id = requestAnimationFrame(() => inputRef.current?.focus());
      return () => cancelAnimationFrame(id);
    }
    setQuery("");
  }, [open]);

  // items 가 바뀌면 인덱스를 0 으로 클램프 — 검색어 변경 시 첫 결과 하이라이트.
  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  const select = useCallback(
    (index: number) => {
      const target = items[index];
      if (!target) return;
      onClose();
      router.push(target.path);
    },
    [items, onClose, router],
  );

  const handleKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((i) => (items.length === 0 ? 0 : (i + 1) % items.length));
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((i) =>
        items.length === 0 ? 0 : (i - 1 + items.length) % items.length,
      );
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      select(activeIndex);
    }
  };

  if (!open) return null;

  return (
    <div
      // 클릭/ESC 양 경로로 닫힘 — 포커스 trap 은 Tab 순환만 보장 (간이 구현).
      role="dialog"
      aria-modal="true"
      aria-label="명령어 팔레트"
      data-testid="command-palette"
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 px-4 pt-[10vh]"
      onClick={onClose}
      onKeyDown={handleKey}
    >
      <div
        // 내부 클릭은 닫지 않는다.
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-xl overflow-hidden rounded-(--radius-l) border border-(--border) bg-(--card-elevated) shadow-[var(--shadow-l)]"
      >
        <div className="border-b border-(--border) px-4 py-3">
          <input
            ref={inputRef}
            type="search"
            value={query}
            onChange={(e) => setQuery(e.currentTarget.value)}
            placeholder="영역·설정 검색 — 예: cron, secret, llm"
            aria-label="검색어"
            data-testid="command-palette-input"
            className="h-8 w-full bg-transparent text-sm text-(--foreground) placeholder:text-(--placeholder) focus:outline-none"
          />
        </div>

        <ul
          role="listbox"
          aria-label="검색 결과"
          data-testid="command-palette-list"
          className="max-h-80 overflow-y-auto py-1"
        >
          {items.length === 0 ? (
            <li
              role="option"
              aria-selected={false}
              data-testid="command-palette-empty"
              className="px-4 py-6 text-center text-sm text-(--muted-foreground)"
            >
              일치하는 영역이 없습니다.
            </li>
          ) : (
            items.map((area, idx) => {
              const active = idx === activeIndex;
              return (
                <li
                  key={area.path}
                  role="option"
                  aria-selected={active}
                  data-active={active || undefined}
                  data-testid={`command-palette-item-${area.path.slice(1)}`}
                  onMouseEnter={() => setActiveIndex(idx)}
                  onClick={() => select(idx)}
                  className={cn(
                    "flex cursor-pointer items-center gap-3 px-4 py-2 text-sm",
                    active
                      ? "bg-(--nav-active-bg) text-(--primary)"
                      : "text-(--foreground)",
                  )}
                >
                  <span aria-hidden className="w-5 text-center">
                    {area.icon}
                  </span>
                  <span className="flex-1">
                    <span className="font-medium">{area.label}</span>
                    <span className="ml-2 font-mono text-xs text-(--muted-foreground)">
                      {area.path}
                    </span>
                  </span>
                  {active ? (
                    <kbd className="rounded-(--radius-sm) border border-(--border) bg-(--card) px-1.5 py-0.5 font-mono text-[11px] text-(--muted-foreground)">
                      ↵
                    </kbd>
                  ) : null}
                </li>
              );
            })
          )}
        </ul>

        <div className="flex items-center justify-between border-t border-(--border) bg-(--card) px-4 py-2 text-xs text-(--muted-foreground)">
          <span>↑↓ 이동 · ↵ 이동 · ESC 닫기</span>
          <span className="font-mono">{items.length} 결과</span>
        </div>
      </div>
    </div>
  );
}

/**
 * useCommandPaletteHotkey — ⌘K / Ctrl+K 글로벌 핫키 바인딩.
 *
 * Shell layout 에서 한 번 호출하면 어떤 라우트에서도 동일하게 동작.
 * 입력 중인 폼 안에서도 떠야 운영자가 흐름을 끊지 않고 점프할 수 있음
 * (DESIGN.md §10.2 키보드 시나리오 "어떤 화면에서든 ⌘K").
 */
export function useCommandPaletteHotkey(onTrigger: () => void) {
  useEffect(() => {
    const handler = (e: globalThis.KeyboardEvent) => {
      const isK = e.key === "k" || e.key === "K";
      if (isK && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        onTrigger();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onTrigger]);
}
