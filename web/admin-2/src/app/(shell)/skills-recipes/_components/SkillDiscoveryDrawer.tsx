"use client";

/**
 * SkillDiscoveryDrawer — admin.pen `GnNLO` 의 BIZ-109 P1 신규 (BIZ-91 Patch B 시각화).
 *
 * 우측 슬라이드 인 패널 — Modal 보다 콘텐츠 영역(좌측) 을 가리지 않고 검색·추가 작업을 하기 위해.
 * 폭은 480px(고정) — admin.pen 의 우측 패널 시각 spec.
 *
 * 구성 (위 → 아래):
 *  1) 헤더 — 타이틀 + 닫기 버튼
 *  2) 검색 입력 — `placeholder = "스킬 이름·키워드"`. 입력값은 카탈로그 client-side 필터링.
 *  3) 카탈로그 카드 목록 — 카테고리 그룹별로 노출. 이미 설치된 스킬은 "설치됨" 뱃지 + 추가 버튼 비활성.
 *  4) 빈 결과 — EmptyState (검색어가 비어있을 때는 노출하지 않음).
 */

import { useEffect, useMemo, useState } from "react";
import { Badge } from "@/design/atoms/Badge";
import { Button } from "@/design/atoms/Button";
import { Input } from "@/design/atoms/Input";
import { EmptyState } from "@/design/molecules/EmptyState";
import { cn } from "@/lib/cn";
import type { CatalogSkill } from "../_data";

interface SkillDiscoveryDrawerProps {
  open: boolean;
  /** 카탈로그 — 부모가 정렬·필터를 미리 적용하지 않고 그대로 넘긴다. */
  catalog: readonly CatalogSkill[];
  /** 현재 설치된 스킬 id 집합 — 카드의 "설치됨" 뱃지 표시에 사용. */
  installedIds?: readonly string[];
  onClose: () => void;
  /** 카드 "추가" 버튼 — 부모가 fixture/state 갱신 + 토스트 표시 담당. */
  onAdd: (skill: CatalogSkill) => void;
}

export function SkillDiscoveryDrawer({
  open,
  catalog,
  installedIds,
  onClose,
  onAdd,
}: SkillDiscoveryDrawerProps) {
  const [query, setQuery] = useState("");

  // 닫힐 때마다 검색어 초기화 — 다음 진입을 깨끗한 상태로.
  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  // ESC 로 닫기 — Modal 패턴과 일관.
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  const installedSet = useMemo(
    () => new Set(installedIds ?? []),
    [installedIds],
  );

  // 검색 필터 — name / description / keywords / category 모두 부분일치.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return catalog;
    return catalog.filter((c) => {
      if (c.name.toLowerCase().includes(q)) return true;
      if (c.description.toLowerCase().includes(q)) return true;
      if (c.category.toLowerCase().includes(q)) return true;
      return c.keywords.some((k) => k.toLowerCase().includes(q));
    });
  }, [catalog, query]);

  // 카테고리별 그룹 — admin.pen 의 카탈로그 그룹 spec.
  const groups = useMemo(() => groupByCategory(filtered), [filtered]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="스킬 카탈로그"
      data-testid="skill-discovery-drawer"
      className="fixed inset-0 z-40 flex justify-end bg-black/40"
      onClick={onClose}
    >
      <aside
        onClick={(e) => e.stopPropagation()}
        className={cn(
          "flex h-full w-full max-w-md flex-col gap-4 border-l border-(--border)",
          "bg-(--card-elevated) shadow-[var(--shadow-l)]",
        )}
      >
        <header className="flex items-start justify-between gap-3 border-b border-(--border) px-5 py-4">
          <div className="flex flex-col gap-0.5">
            <h2 className="text-lg font-semibold text-(--foreground-strong)">
              스킬 카탈로그
            </h2>
            <p className="text-xs text-(--muted-foreground)">
              검색해서 운영자 워크스페이스에 새 스킬을 추가합니다.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="닫기"
            data-testid="skill-discovery-close"
            className="-mr-2 -mt-1 inline-flex h-8 w-8 items-center justify-center rounded-(--radius-m) text-(--muted-foreground) hover:bg-(--surface) hover:text-(--foreground)"
          >
            ×
          </button>
        </header>

        <div className="px-5">
          <Input
            value={query}
            onChange={(e) => setQuery(e.currentTarget.value)}
            placeholder="스킬 이름·키워드"
            leading={<span aria-hidden>⌕</span>}
            data-testid="skill-discovery-search"
            autoFocus
          />
        </div>

        <div className="flex-1 overflow-y-auto px-5 pb-5">
          {filtered.length === 0 ? (
            <div data-testid="skill-discovery-empty">
              <EmptyState
                title="검색 결과가 없어요"
                description={`"${query}" 와 일치하는 카탈로그 스킬이 없습니다. 다른 키워드로 시도해 보세요.`}
              />
            </div>
          ) : (
            <ul
              data-testid="skill-discovery-list"
              className="flex flex-col gap-4"
            >
              {groups.map((group) => (
                <li key={group.category} className="flex flex-col gap-2">
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-(--muted-foreground)">
                    {group.category}
                  </h3>
                  <ul className="flex flex-col gap-2">
                    {group.items.map((item) => {
                      const installed =
                        item.installed || installedSet.has(item.id);
                      return (
                        <li
                          key={item.id}
                          data-testid={`catalog-skill-${item.id}`}
                          data-installed={installed || undefined}
                          className="flex items-start justify-between gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-3"
                        >
                          <div className="flex min-w-0 flex-col gap-1">
                            <div className="flex items-center gap-2">
                              <span className="truncate text-sm font-medium text-(--foreground-strong)">
                                {item.name}
                              </span>
                              <Badge
                                tone={
                                  item.publisher === "simpleclaw"
                                    ? "brand"
                                    : "neutral"
                                }
                                size="sm"
                              >
                                {item.publisher}
                              </Badge>
                              {installed ? (
                                <Badge tone="success" size="sm">
                                  설치됨
                                </Badge>
                              ) : null}
                            </div>
                            <p className="line-clamp-2 text-xs text-(--muted-foreground)">
                              {item.description}
                            </p>
                          </div>
                          <Button
                            size="sm"
                            variant={installed ? "ghost" : "primary"}
                            disabled={installed}
                            onClick={() => onAdd(item)}
                            data-testid={`catalog-skill-${item.id}-add`}
                          >
                            {installed ? "설치됨" : "＋ 추가"}
                          </Button>
                        </li>
                      );
                    })}
                  </ul>
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>
    </div>
  );
}

interface CatalogGroup {
  category: string;
  items: CatalogSkill[];
}

/** 카탈로그를 카테고리 키로 그룹핑 — 입력 순서를 유지한다. */
function groupByCategory(catalog: readonly CatalogSkill[]): CatalogGroup[] {
  const groups: CatalogGroup[] = [];
  const byKey = new Map<string, CatalogGroup>();
  for (const item of catalog) {
    let group = byKey.get(item.category);
    if (!group) {
      group = { category: item.category, items: [] };
      byKey.set(item.category, group);
      groups.push(group);
    }
    group.items.push(item);
  }
  return groups;
}
