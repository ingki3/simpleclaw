/**
 * /skills-recipes — Admin 2.0 S6 (BIZ-117).
 *
 * admin.pen `GnNLO` (Skills & Recipes Shell) 의 콘텐츠 영역을 React 로 박제한다.
 * 구성 (위 → 아래):
 *  1) 페이지 헤더 — h1 "스킬 & 레시피" + 한 줄 설명 + 검색 입력 + "카탈로그 열기" 버튼.
 *  2) SkillsList — 설치된 스킬 카드 그리드.
 *     `?skills=loading|empty|error` 쿼리로 4-variant 검증.
 *  3) RecipesList — 레시피 카드 그리드.
 *     `?recipes=loading|empty|error` 쿼리로 4-variant 검증.
 *  4) SkillDiscoveryDrawer (BIZ-109 P1) — 우측 슬라이드 in 카탈로그.
 *  5) RetryPolicyModal (BIZ-109 P1) — 인라인 정책 편집.
 *
 * 본 단계는 정적 fixture 만 사용 — 데이터 소스 연결은 데몬 통합 단계에서 교체.
 * 토글/추가/저장은 로컬 상태만 갱신하고 console 로 박제 (실제 mutation 은 후속 sub-issue).
 */
"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { findAreaByPath } from "@/app/areas";
import { Badge } from "@/design/atoms/Badge";
import { Button } from "@/design/atoms/Button";
import { Input } from "@/design/atoms/Input";
import {
  SkillsList,
  type SkillsListState,
} from "./_components/SkillsList";
import { RecipesList } from "./_components/RecipesList";
import { SkillDiscoveryDrawer } from "./_components/SkillDiscoveryDrawer";
import { RetryPolicyModal } from "./_components/RetryPolicyModal";
import {
  getSkillsRecipesSnapshot,
  type CatalogSkill,
  type InstalledSkill,
  type Recipe,
  type RetryPolicy,
} from "./_data";

const VALID_LIST_STATES: readonly SkillsListState[] = [
  "default",
  "empty",
  "loading",
  "error",
];

export default function SkillsRecipesPage() {
  return (
    <Suspense fallback={null}>
      <SkillsRecipesContent />
    </Suspense>
  );
}

function SkillsRecipesContent() {
  const area = findAreaByPath("/skills-recipes");
  const snapshot = useMemo(() => getSkillsRecipesSnapshot(), []);

  // 4-variant 쿼리 — `?skills=`, `?recipes=` 가 각자 독립 매개변수.
  const params = useSearchParams();
  const skillsState = readState(params.get("skills"));
  const recipesState = readState(params.get("recipes"));

  // 로컬 상태 — fixture 를 mutable 카피로 들고 있으면서 토글/추가가 즉시 반영되도록.
  const [installedSkills, setInstalledSkills] = useState<InstalledSkill[]>(
    () => [...snapshot.skills],
  );
  const [recipes, setRecipes] = useState<Recipe[]>(() => [...snapshot.recipes]);
  const [search, setSearch] = useState("");
  const [discoveryOpen, setDiscoveryOpen] = useState(false);
  const [policyTarget, setPolicyTarget] = useState<InstalledSkill | null>(null);

  // empty variant 일 때는 fixture 를 비워서 EmptyState 가 노출되도록 — variant 검증.
  const skillsForRender =
    skillsState === "empty" ? [] : applyFilter(installedSkills, search);
  const recipesForRender =
    recipesState === "empty" ? [] : applyFilter(recipes, search);

  const installedIds = useMemo(
    () => installedSkills.map((s) => s.id),
    [installedSkills],
  );

  return (
    <section
      className="mx-auto flex max-w-6xl flex-col gap-6 p-8"
      data-testid="skills-recipes-page"
    >
      <header className="flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <div className="flex flex-col gap-1">
            <h1 className="text-2xl font-semibold text-(--foreground-strong)">
              {area?.label ?? "스킬 & 레시피"}
            </h1>
            <p className="text-sm text-(--muted-foreground)">
              내장 스킬과 운영자 레시피 카탈로그를 관리합니다 (DESIGN.md §4.4).
            </p>
          </div>
          <div
            data-testid="skills-recipes-counts"
            className="flex items-center gap-2 text-xs text-(--muted-foreground)"
          >
            <Badge tone="neutral">스킬 {installedSkills.length}</Badge>
            <Badge tone="neutral">레시피 {recipes.length}</Badge>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="min-w-[260px] flex-1">
            <Input
              value={search}
              onChange={(e) => setSearch(e.currentTarget.value)}
              placeholder="이름·설명·트리거로 검색"
              leading={<span aria-hidden>⌕</span>}
              data-testid="skills-recipes-search"
            />
          </div>
          <Button
            variant="primary"
            onClick={() => setDiscoveryOpen(true)}
            data-testid="skills-recipes-discover"
          >
            ＋ 카탈로그 열기
          </Button>
        </div>
      </header>

      <SectionHeader id="skills" title="스킬" subtitle="설치된 스킬 — 활성/비활성 토글과 재시도 정책 편집." />
      <SkillsList
        state={skillsState}
        skills={skillsForRender}
        searchQuery={search}
        onToggleEnabled={(id, next) => {
          setInstalledSkills((cur) =>
            cur.map((s) => (s.id === id ? { ...s, enabled: next } : s)),
          );
          if (typeof console !== "undefined") {
            console.info("[skills-recipes] toggle skill", id, next);
          }
        }}
        onEditPolicy={(skill) => setPolicyTarget(skill)}
        onDiscover={() => setDiscoveryOpen(true)}
        onRetry={() => {
          if (typeof console !== "undefined") {
            console.info("[skills-recipes] retry skills fetch");
          }
        }}
      />

      <SectionHeader id="recipes" title="레시피" subtitle="레시피 — 트리거 → 스킬/명령어/지시문 단계." />
      <RecipesList
        state={recipesState}
        recipes={recipesForRender}
        searchQuery={search}
        onToggleEnabled={(id, next) => {
          setRecipes((cur) =>
            cur.map((r) => (r.id === id ? { ...r, enabled: next } : r)),
          );
          if (typeof console !== "undefined") {
            console.info("[skills-recipes] toggle recipe", id, next);
          }
        }}
        onRetry={() => {
          if (typeof console !== "undefined") {
            console.info("[skills-recipes] retry recipes fetch");
          }
        }}
      />

      <SkillDiscoveryDrawer
        open={discoveryOpen}
        catalog={snapshot.catalog}
        installedIds={installedIds}
        onClose={() => setDiscoveryOpen(false)}
        onAdd={(catalogSkill) => {
          setInstalledSkills((cur) => addFromCatalog(cur, catalogSkill));
          if (typeof console !== "undefined") {
            console.info("[skills-recipes] add skill", catalogSkill.id);
          }
        }}
      />

      <RetryPolicyModal
        open={policyTarget !== null}
        skill={policyTarget}
        onClose={() => setPolicyTarget(null)}
        onSubmit={(skillId, nextPolicy) => {
          setInstalledSkills((cur) =>
            cur.map((s) =>
              s.id === skillId ? { ...s, retryPolicy: nextPolicy } : s,
            ),
          );
          if (typeof console !== "undefined") {
            console.info("[skills-recipes] save retry policy", skillId, nextPolicy);
          }
        }}
      />
    </section>
  );
}

function SectionHeader({
  id,
  title,
  subtitle,
}: {
  id: string;
  title: string;
  subtitle: string;
}) {
  return (
    <header className="flex flex-col gap-0.5" id={id} data-testid={`section-${id}`}>
      <h2 className="text-base font-semibold text-(--foreground-strong)">
        {title}
      </h2>
      <p className="text-xs text-(--muted-foreground)">{subtitle}</p>
    </header>
  );
}

/** ?skills=foo / ?recipes=foo 의 4-variant 매핑 — 알 수 없으면 default. */
function readState(raw: string | null): SkillsListState {
  if (raw && (VALID_LIST_STATES as readonly string[]).includes(raw)) {
    return raw as SkillsListState;
  }
  return "default";
}

interface NameDescription {
  name: string;
  description: string;
  trigger?: string;
}

/** 검색 — name/description/trigger substring. */
function applyFilter<T extends NameDescription>(
  items: readonly T[],
  query: string,
): T[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...items];
  return items.filter((item) => {
    if (item.name.toLowerCase().includes(q)) return true;
    if (item.description.toLowerCase().includes(q)) return true;
    if (item.trigger && item.trigger.toLowerCase().includes(q)) return true;
    return false;
  });
}

/** 카탈로그에서 InstalledSkill 로 승격 — 기본 정책/메타로 박제. */
function addFromCatalog(
  current: InstalledSkill[],
  catalog: CatalogSkill,
): InstalledSkill[] {
  // 이미 설치된 경우 변동 없음 — Drawer UI 가 disable 시키지만 race 보호.
  if (current.some((s) => s.id === catalog.id)) return current;
  const defaults: InstalledSkill = {
    id: catalog.id,
    name: catalog.name,
    description: catalog.description,
    source: catalog.source,
    userInvocable: catalog.userInvocable,
    enabled: true,
    directory: `~/.agents/skills/${catalog.name}`,
    argumentHint: undefined,
    retryPolicy: defaultRetryPolicy(),
    lastRun: null,
    health: { tone: "neutral", label: "실행 이력 없음" },
  };
  return [...current, defaults];
}

function defaultRetryPolicy(): RetryPolicy {
  return {
    maxAttempts: 2,
    backoffSeconds: 1,
    backoffStrategy: "exponential",
    timeoutSeconds: 30,
  };
}
