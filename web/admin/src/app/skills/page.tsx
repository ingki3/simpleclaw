"use client";

/**
 * Skills & Recipes 화면 — BIZ-47.
 *
 * 좌측: 스킬 목록(검색 + 카드 그리드) / 우측: 선택된 스킬 상세 Drawer.
 * 탭 전환으로 레시피 목록을 표시하며, 양쪽 모두 활성/비활성 토글은 dry-run 없이
 * 즉시 반영되고(↻) 토스트로 결과를 보고한다(Undo 포함).
 *
 * 데이터 흐름:
 *  - 마운트 시 스킬·레시피를 병렬 fetch.
 *  - 토글은 낙관적 업데이트(optimistic) → API 호출 실패 시 자동 롤백.
 *  - 백엔드(``/admin/v1/skills/*``, ``/admin/v1/recipes/*``)가 BIZ-41 후속에서 신설될
 *    예정이므로, 개발 빌드는 mock 폴백을 사용한다(``lib/skills-api.ts``).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Search, Wrench } from "lucide-react";
import { Tabs } from "@/components/atoms/Tabs";
import { Input } from "@/components/atoms/Input";
import { Button } from "@/components/atoms/Button";
import { SkillCard } from "@/components/domain/SkillCard";
import { RecipeCard } from "@/components/domain/RecipeCard";
import { SkillDetailDrawer } from "@/components/domain/SkillDetailDrawer";
import {
  getSkill,
  listRecipes,
  listSkills,
  patchRecipe,
  patchSkill,
} from "@/lib/skills-api";
import type {
  Recipe,
  RetryPolicy,
  Skill,
  SkillDetail,
} from "@/lib/skills-types";
import { useToast } from "@/lib/toast";

type Tab = "skills" | "recipes";

export default function SkillsPage() {
  const toast = useToast();

  const [tab, setTab] = useState<Tab>("skills");
  const [skills, setSkills] = useState<Skill[]>([]);
  const [recipes, setRecipes] = useState<Recipe[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");

  const [selected, setSelected] = useState<SkillDetail | null>(null);

  // 초기 로드 — 두 영역을 병렬로 받는다.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([listSkills(), listRecipes()])
      .then(([s, r]) => {
        if (cancelled) return;
        setSkills(s);
        setRecipes(r);
      })
      .catch(() => {
        if (cancelled) return;
        toast.push({
          tone: "error",
          title: "불러오기에 실패했습니다",
          description: "잠시 후 다시 시도해 주세요.",
        });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [toast]);

  // 검색 필터(name + description) — 1차에는 client-side substring 매칭으로 충분.
  const filteredSkills = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return skills;
    return skills.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q),
    );
  }, [skills, filter]);

  const filteredRecipes = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return recipes;
    return recipes.filter(
      (r) =>
        r.name.toLowerCase().includes(q) ||
        r.description.toLowerCase().includes(q),
    );
  }, [recipes, filter]);

  // 카드 선택 → 상세 Drawer 오픈. 디테일은 별도 GET으로 받는다(SKILL.md 본문 포함).
  const openDetail = useCallback(
    async (id: string) => {
      try {
        const detail = await getSkill(id);
        setSelected(detail);
      } catch {
        toast.push({
          tone: "error",
          title: "상세 정보를 불러오지 못했어요",
        });
      }
    },
    [toast],
  );

  /**
   * 스킬 활성/비활성 토글 — DoD: dry-run 없이 즉시 반영 + 토스트(undo).
   *
   * 낙관적 업데이트 전략:
   *  1) 로컬 state를 먼저 갱신해 토글이 즉시 보이도록 한다.
   *  2) PATCH 호출. 성공 시 토스트(Undo 콜백 포함), 실패 시 이전 상태로 자동 롤백.
   */
  const toggleSkill = useCallback(
    async (id: string, next: boolean) => {
      const prev = skills.find((s) => s.id === id);
      if (!prev) return;
      setSkills((cur) =>
        cur.map((s) => (s.id === id ? { ...s, enabled: next } : s)),
      );
      if (selected?.id === id) {
        setSelected({ ...selected, enabled: next });
      }
      try {
        await patchSkill(id, { enabled: next });
        toast.push({
          tone: "success",
          title: `${prev.name} ${next ? "활성화" : "비활성화"}됨`,
          description: next ? "에이전트가 다시 호출할 수 있어요." : "다음 호출부터 무시됩니다.",
          onUndo: () => toggleSkill(id, !next),
        });
      } catch {
        // 롤백
        setSkills((cur) =>
          cur.map((s) =>
            s.id === id ? { ...s, enabled: prev.enabled } : s,
          ),
        );
        if (selected?.id === id) {
          setSelected({ ...selected, enabled: prev.enabled });
        }
        toast.push({
          tone: "error",
          title: "토글 적용 실패",
          description: "스킬 상태를 되돌렸어요.",
        });
      }
    },
    [skills, selected, toast],
  );

  const toggleRecipe = useCallback(
    async (id: string, next: boolean) => {
      const prev = recipes.find((r) => r.id === id);
      if (!prev) return;
      setRecipes((cur) =>
        cur.map((r) => (r.id === id ? { ...r, enabled: next } : r)),
      );
      try {
        await patchRecipe(id, { enabled: next });
        toast.push({
          tone: "success",
          title: `/${prev.name} ${next ? "활성화" : "비활성화"}됨`,
          onUndo: () => toggleRecipe(id, !next),
        });
      } catch {
        setRecipes((cur) =>
          cur.map((r) => (r.id === id ? { ...r, enabled: prev.enabled } : r)),
        );
        toast.push({
          tone: "error",
          title: "레시피 토글 실패",
        });
      }
    },
    [recipes, toast],
  );

  // 정책 저장 — Drawer footer "정책 저장" 클릭 시 호출.
  const saveSkillPolicy = useCallback(
    async (id: string, policy: RetryPolicy) => {
      try {
        await patchSkill(id, { retry_policy: policy });
        setSkills((cur) =>
          cur.map((s) =>
            s.id === id ? { ...s, retry_policy: policy } : s,
          ),
        );
        if (selected?.id === id) {
          setSelected({ ...selected, retry_policy: policy });
        }
        toast.push({
          tone: "success",
          title: "재시도 정책 저장됨",
          description: `최대 ${policy.max_attempts}회 · ${policy.backoff_strategy}`,
        });
      } catch {
        toast.push({
          tone: "error",
          title: "정책 저장 실패",
          description: "변경 내용을 적용하지 못했어요.",
        });
      }
    },
    [selected, toast],
  );

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-center gap-3">
        <Tabs<Tab>
          ariaLabel="스킬과 레시피"
          items={[
            { value: "skills", label: "스킬", count: skills.length },
            { value: "recipes", label: "레시피", count: recipes.length },
          ]}
          value={tab}
          onValueChange={setTab}
        />
        <div className="ml-auto w-full max-w-[320px]">
          <Input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder={
              tab === "skills" ? "스킬 이름·설명으로 검색" : "레시피 검색"
            }
            leftIcon={<Search size={14} aria-hidden />}
          />
        </div>
      </header>

      {loading ? (
        <ListSkeleton />
      ) : tab === "skills" ? (
        <SkillsList
          skills={filteredSkills}
          totalCount={skills.length}
          selectedId={selected?.id}
          onSelect={openDetail}
          onToggleEnabled={toggleSkill}
        />
      ) : (
        <RecipesList
          recipes={filteredRecipes}
          totalCount={recipes.length}
          onToggleEnabled={toggleRecipe}
        />
      )}

      <SkillDetailDrawer
        skill={selected}
        onClose={() => setSelected(null)}
        onToggleEnabled={toggleSkill}
        onSavePolicy={saveSkillPolicy}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-views
// ---------------------------------------------------------------------------

function SkillsList({
  skills,
  totalCount,
  selectedId,
  onSelect,
  onToggleEnabled,
}: {
  skills: Skill[];
  totalCount: number;
  selectedId?: string;
  onSelect: (id: string) => void;
  onToggleEnabled: (id: string, next: boolean) => void;
}) {
  if (totalCount === 0) {
    return (
      <EmptyCard
        title="발견된 스킬이 없어요"
        description="`.agent/skills/` 또는 `~/.agents/skills/`에 SKILL.md를 추가하면 여기에 표시됩니다."
        actionLabel="스킬 가이드 열기"
        actionHref="https://github.com/ingki3/SimpleClaw/blob/dev/docs/feature-skills.md"
      />
    );
  }
  if (skills.length === 0) {
    return (
      <EmptyCard
        title="검색 결과가 없어요"
        description="다른 키워드로 다시 시도해 보세요."
      />
    );
  }
  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-3">
      {skills.map((s) => (
        <SkillCard
          key={s.id}
          skill={s}
          selected={selectedId === s.id}
          onSelect={onSelect}
          onToggleEnabled={onToggleEnabled}
        />
      ))}
    </div>
  );
}

function RecipesList({
  recipes,
  totalCount,
  onToggleEnabled,
}: {
  recipes: Recipe[];
  totalCount: number;
  onToggleEnabled: (id: string, next: boolean) => void;
}) {
  if (totalCount === 0) {
    return (
      <EmptyCard
        title="등록된 레시피가 없어요"
        description="`recipes/`에 YAML을 추가하면 여기에 표시됩니다. 가장 흔한 출발은 매일 아침 브리핑이에요."
        actionLabel="레시피 가이드 열기"
        actionHref="https://github.com/ingki3/SimpleClaw/blob/dev/docs/feature-recipes.md"
      />
    );
  }
  if (recipes.length === 0) {
    return (
      <EmptyCard
        title="검색 결과가 없어요"
        description="다른 키워드로 다시 시도해 보세요."
      />
    );
  }
  return (
    <div className="flex flex-col gap-3">
      {recipes.map((r) => (
        <RecipeCard
          key={r.id}
          recipe={r}
          onToggleEnabled={onToggleEnabled}
        />
      ))}
    </div>
  );
}

function ListSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 6 }).map((_, i) => (
        <div
          key={i}
          aria-hidden
          className="h-[140px] animate-pulse rounded-[--radius-l] border border-[--border-divider] bg-[--surface]"
        />
      ))}
    </div>
  );
}

function EmptyCard({
  title,
  description,
  actionLabel,
  actionHref,
}: {
  title: string;
  description: string;
  actionLabel?: string;
  actionHref?: string;
}) {
  return (
    <section className="mx-auto flex max-w-[480px] flex-col items-center gap-3 rounded-[--radius-l] border border-dashed border-[--border-strong] bg-[--card] px-8 py-16 text-center">
      <Wrench
        size={32}
        strokeWidth={1.5}
        aria-hidden
        className="text-[--muted-foreground]"
      />
      <h2 className="text-xl font-semibold text-[--foreground-strong]">
        {title}
      </h2>
      <p className="text-sm text-[--muted-foreground]">{description}</p>
      {actionLabel && actionHref ? (
        <a href={actionHref} target="_blank" rel="noreferrer">
          <Button variant="outline" size="sm">
            {actionLabel}
          </Button>
        </a>
      ) : null}
    </section>
  );
}
