"use client";

/**
 * Command Palette — DESIGN.md §4.8.
 *
 * 결과 카테고리(상위 → 하위 우선순위):
 *   1) 화면 이동(NAV_ITEMS, 11개 라우트)
 *   2) 설정 키 (SETTING_KEYS) — Enter 시 ``/{area}?focus=<key>``로 이동
 *   3) 시크릿 (이름만, 값은 절대 노출 안 함) — Enter 시 ``/secrets?focus=<name>``
 *
 * 시크릿 인덱스는 prop으로 외부 주입(``secrets``) 받아 ``useAdminQuery('/secrets')``의
 * 결과를 그대로 흘려도 되고, 미주입 시 빈 카테고리로 처리된다.
 *
 * 구현 노트:
 *  - role="dialog" + aria-modal로 modal-like 시맨틱.
 *  - Escape / 바깥 클릭으로 닫기. 진입 시 input 자동 포커스.
 *  - 단축키(⌘K) 자체는 Topbar에서 토글한다.
 *  - 키보드 ↑/↓로 결과 사이 이동, Enter로 선택.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Search, KeyRound, Settings as SettingsIcon, ArrowRight } from "lucide-react";
import { NAV_ITEMS } from "@/lib/nav";
import { getIcon } from "@/lib/icon";
import { cn } from "@/lib/cn";
import { SETTING_KEYS, AREA_TO_ROUTE } from "@/lib/setting-keys";
import type { SecretMeta } from "@/lib/api";

export interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 시크릿 메타 — 보통 ``useAdminQuery<ListSecretsResponse>('/secrets')``의 결과. */
  secrets?: ReadonlyArray<SecretMeta>;
}

type Result =
  | {
      kind: "page";
      id: string;
      label: string;
      hint: string;
      icon: string;
      href: string;
    }
  | {
      kind: "setting";
      id: string;
      label: string;
      hint: string;
      key: string;
      href: string;
    }
  | {
      kind: "secret";
      id: string;
      label: string;
      hint: string;
      name: string;
      href: string;
    };

const MAX_PER_GROUP = 6;

export function CommandPalette({
  open,
  onOpenChange,
  secrets,
}: CommandPaletteProps) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // 진입 시 input 포커스 + 닫힘 시 query 초기화.
  useEffect(() => {
    if (open) {
      setQuery("");
      setActiveIndex(0);
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

  const flatResults = useMemo<Result[]>(() => {
    const q = query.trim().toLowerCase();
    const matchPage = (n: (typeof NAV_ITEMS)[number]) =>
      !q ||
      n.label.toLowerCase().includes(q) ||
      n.href.toLowerCase().includes(q) ||
      n.description.toLowerCase().includes(q);
    const matchSetting = (s: (typeof SETTING_KEYS)[number]) =>
      !q ||
      s.key.toLowerCase().includes(q) ||
      s.label.toLowerCase().includes(q);
    const matchSecret = (s: SecretMeta) =>
      !q || s.name.toLowerCase().includes(q);

    const pages: Result[] = NAV_ITEMS.filter(matchPage)
      .slice(0, MAX_PER_GROUP)
      .map((n) => ({
        kind: "page",
        id: `page:${n.href}`,
        label: n.label,
        hint: n.href,
        icon: n.icon,
        href: n.href,
      }));

    const settings: Result[] = SETTING_KEYS.filter(matchSetting)
      .slice(0, MAX_PER_GROUP)
      .map((s) => ({
        kind: "setting",
        id: `setting:${s.key}`,
        label: s.label,
        hint: s.key,
        key: s.key,
        href: `${AREA_TO_ROUTE[s.area]}?focus=${encodeURIComponent(s.key)}`,
      }));

    const secretsList: Result[] = (secrets ?? [])
      .filter(matchSecret)
      .slice(0, MAX_PER_GROUP)
      .map((s) => ({
        kind: "secret",
        id: `secret:${s.name}`,
        label: s.name,
        hint: `${s.backend} · 마스킹됨`,
        name: s.name,
        href: `/secrets?focus=${encodeURIComponent(s.name)}`,
      }));

    return [...pages, ...settings, ...secretsList];
  }, [query, secrets]);

  // query/결과가 바뀌면 active를 처음으로.
  useEffect(() => {
    setActiveIndex(0);
  }, [query, secrets]);

  function commit(result: Result) {
    onOpenChange(false);
    router.push(result.href);
  }

  function handleKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, flatResults.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter") {
      const r = flatResults[activeIndex];
      if (r) {
        e.preventDefault();
        commit(r);
      }
    }
  }

  if (!open) return null;

  // 그룹 시각 분할 — 같은 카테고리는 함께 묶어서 sticky 라벨로.
  const groups: Array<{ kind: Result["kind"]; items: Result[]; label: string }> = [];
  for (const r of flatResults) {
    const last = groups[groups.length - 1];
    if (last && last.kind === r.kind) {
      last.items.push(r);
    } else {
      groups.push({
        kind: r.kind,
        label:
          r.kind === "page"
            ? "화면"
            : r.kind === "setting"
              ? "설정"
              : "시크릿",
        items: [r],
      });
    }
  }

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
            onKeyDown={handleKey}
            placeholder="화면, 설정 키, 시크릿 이름…"
            className="flex-1 bg-transparent text-sm text-[--foreground] placeholder:text-[--placeholder] outline-none"
            aria-label="명령 검색"
          />
          <kbd className="rounded-[--radius-sm] border border-[--border] bg-[--background] px-1.5 py-0.5 font-mono text-[10px] text-[--muted-foreground]">
            Esc
          </kbd>
        </div>
        <ul className="max-h-[50vh] overflow-y-auto py-1" role="listbox">
          {flatResults.length === 0 ? (
            <li className="px-4 py-8 text-center text-sm text-[--muted-foreground]">
              결과가 없어요.
            </li>
          ) : (
            groups.map((g) => (
              <li key={g.kind}>
                <div className="sticky top-0 z-10 bg-[--card-elevated] px-4 py-1.5 text-[10px] uppercase tracking-wide text-[--muted-foreground]">
                  {g.label}
                </div>
                <ul>
                  {g.items.map((r) => {
                    const indexInFlat = flatResults.indexOf(r);
                    const active = indexInFlat === activeIndex;
                    return (
                      <li key={r.id} role="option" aria-selected={active}>
                        <button
                          type="button"
                          onMouseEnter={() => setActiveIndex(indexInFlat)}
                          onClick={() => commit(r)}
                          className={cn(
                            "flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm text-[--foreground] transition-colors",
                            active
                              ? "bg-[--surface]"
                              : "hover:bg-[--surface]",
                          )}
                        >
                          <ResultIcon r={r} />
                          <span className="flex-1 truncate">{r.label}</span>
                          <span className="ml-auto truncate font-mono text-[11px] text-[--muted-foreground]">
                            {r.hint}
                          </span>
                          <ArrowRight
                            size={12}
                            aria-hidden
                            className={cn(
                              "shrink-0 text-[--muted-foreground] transition-opacity",
                              active ? "opacity-100" : "opacity-0",
                            )}
                          />
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </li>
            ))
          )}
        </ul>
      </div>
    </div>
  );
}

function ResultIcon({ r }: { r: Result }) {
  if (r.kind === "page") {
    const Icon = getIcon(r.icon);
    return (
      <Icon
        size={16}
        aria-hidden
        className="shrink-0 text-[--muted-foreground]"
      />
    );
  }
  if (r.kind === "setting") {
    return (
      <SettingsIcon
        size={16}
        aria-hidden
        className="shrink-0 text-[--muted-foreground]"
      />
    );
  }
  return (
    <KeyRound
      size={16}
      aria-hidden
      className="shrink-0 text-[--muted-foreground]"
    />
  );
}
