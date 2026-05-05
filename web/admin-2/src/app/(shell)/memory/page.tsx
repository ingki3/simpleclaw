/**
 * /memory — Admin 2.0 S8 (BIZ-119).
 *
 * admin.pen `fab0a` (Memory Shell) 의 콘텐츠 영역을 React 로 박제한다.
 * 구성 (위 → 아래):
 *  1) 페이지 헤더 — h1 "기억" + 한 줄 설명 + 마지막 dreaming 메타 + 검색 + Dry-run 버튼.
 *  2) ActiveProjectsPanel (`XFipm`) — 4-variant.
 *  3) Memory clusters (`MemoryClusterMap` reusable) — 클러스터 도넛/리스트.
 *  4) Insights (`oeMlR`) — 4-variant 큐 + Accept/Reject/Source.
 *  5) Blocklist (`lVcRk` 짝꿍) — 차단된 토픽 표.
 *  6) Reject Confirm modal (`lVcRk`), Source Drawer (`ftlSL`),
 *     Dry-run Preview modal (`QBf7N`).
 *
 * 본 단계는 정적 fixture 만 사용 — 데이터 소스 연결은 Memory/Dreaming 데몬
 * 통합 단계에서 교체. 채택/거절/차단해제는 fixture 카피만 갱신하고 console
 * 로 박제 (실제 mutation 은 후속 sub-issue).
 */
"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { findAreaByPath } from "@/app/areas";
import { Badge } from "@/design/atoms/Badge";
import { Button } from "@/design/atoms/Button";
import { Input } from "@/design/atoms/Input";
import { MemoryClusterMap } from "@/design/domain/MemoryClusterMap";
import {
  ActiveProjectsPanel,
  type ActiveProjectsState,
} from "./_components/ActiveProjectsPanel";
import { BlocklistTable } from "./_components/BlocklistTable";
import { DryRunPreviewModal } from "./_components/DryRunPreviewModal";
import {
  InsightsList,
  type InsightsListState,
} from "./_components/InsightsList";
import {
  RejectConfirmModal,
  type RejectConfirmInput,
} from "./_components/RejectConfirmModal";
import { SourceDrawer } from "./_components/SourceDrawer";
import {
  getMemorySnapshot,
  type ActiveProject,
  type BlocklistEntry,
  type MemoryInsight,
} from "./_data";

const VALID_INSIGHTS_STATES: readonly InsightsListState[] = [
  "default",
  "empty",
  "loading",
  "error",
];

const VALID_PROJECTS_STATES: readonly ActiveProjectsState[] = [
  "default",
  "empty",
  "loading",
  "error",
];

export default function MemoryPage() {
  return (
    <Suspense fallback={null}>
      <MemoryContent />
    </Suspense>
  );
}

function MemoryContent() {
  const area = findAreaByPath("/memory");
  const snapshot = useMemo(() => getMemorySnapshot(), []);

  // 4-variant 쿼리 — `?insights=` / `?projects=` 두 매개변수.
  const params = useSearchParams();
  const insightsState = readState(
    params.get("insights"),
    VALID_INSIGHTS_STATES,
  );
  const projectsState = readState(
    params.get("projects"),
    VALID_PROJECTS_STATES,
  );

  // 로컬 상태 — fixture 를 mutable 카피로 들고 있으면서 채택/거절/차단해제가
  // 즉시 반영되도록.
  const [insights, setInsights] = useState<MemoryInsight[]>(() => [
    ...snapshot.insights,
  ]);
  const [projects, setProjects] = useState<ActiveProject[]>(() => [
    ...snapshot.activeProjects,
  ]);
  const [blocklist, setBlocklist] = useState<BlocklistEntry[]>(() => [
    ...snapshot.blocklist,
  ]);
  const [search, setSearch] = useState("");

  // 모달/드로어 트리거 상태 — 단일 활성만 허용.
  const [rejectTarget, setRejectTarget] = useState<MemoryInsight | null>(null);
  const [sourceTarget, setSourceTarget] = useState<MemoryInsight | null>(null);
  const [dryRunOpen, setDryRunOpen] = useState(false);

  // 리뷰 큐는 lifecycle = "review" 만 노출 — Active/Archive 는 후속 sub-issue
  // 가 별도 탭으로 분리할 수 있다 (admin.pen Insights 의 4-탭 spec).
  const reviewInsights = useMemo(
    () =>
      applyFilter(
        insights.filter((i) => i.lifecycle === "review"),
        search,
      ),
    [insights, search],
  );

  // 페이지 헤더 우측 카운트 — "검토 N · 활성 N · 차단 N".
  const counts = useMemo(() => summarizeCounts(insights, blocklist), [
    insights,
    blocklist,
  ]);

  return (
    <section
      className="mx-auto flex max-w-6xl flex-col gap-6 p-8"
      data-testid="memory-page"
    >
      <header className="flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <div className="flex flex-col gap-1">
            <h1 className="text-2xl font-semibold text-(--foreground-strong)">
              {area?.label ?? "기억"}
            </h1>
            <p className="text-sm text-(--muted-foreground)">
              대화·드리밍·Active Projects 를 한 화면에서 관리합니다 (DESIGN.md §3.6).
            </p>
            <DreamingStatusLine
              lastDreaming={snapshot.lastDreaming}
              data-testid="memory-dreaming-meta"
            />
          </div>
          <div
            data-testid="memory-counts"
            className="flex items-center gap-2 text-xs text-(--muted-foreground)"
          >
            <Badge tone="brand">검토 {counts.review}</Badge>
            <Badge tone="success">활성 {counts.active}</Badge>
            <Badge tone={counts.blocked > 0 ? "danger" : "neutral"}>
              차단 {counts.blocked}
            </Badge>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="min-w-[260px] flex-1">
            <Input
              value={search}
              onChange={(e) => setSearch(e.currentTarget.value)}
              placeholder="토픽·본문으로 검색"
              leading={<span aria-hidden>⌕</span>}
              data-testid="memory-search"
            />
          </div>
          <Button
            variant="secondary"
            onClick={() => setDryRunOpen(true)}
            data-testid="memory-dry-run"
          >
            Dry-run Preview
          </Button>
        </div>
      </header>

      <SectionHeader
        id="active-projects"
        title="Active Projects"
        subtitle="dreaming 사이드카가 채택한 managed 프로젝트 — XFipm mirror."
      />
      <ActiveProjectsPanel
        state={projectsState}
        projects={projectsState === "empty" ? [] : projects}
        onToggleManaged={(id, next) => {
          setProjects((cur) =>
            cur.map((p) => (p.id === id ? { ...p, managed: next } : p)),
          );
          if (typeof console !== "undefined") {
            console.info("[memory] toggle managed", id, next);
          }
        }}
        onRetry={() => {
          if (typeof console !== "undefined") {
            console.info("[memory] retry active projects");
          }
        }}
      />

      <SectionHeader
        id="clusters"
        title="Memory clusters"
        subtitle="dreaming 이 추출한 클러스터 분포 + 상위 키워드."
      />
      <MemoryClusterMap clusters={snapshot.clusters} />

      <SectionHeader
        id="insights"
        title={`Insights · 검토 큐 (${reviewInsights.length})`}
        subtitle="채택하면 USER.md 에 적용 · 거절하면 블록리스트에 등록."
      />
      <InsightsList
        state={insightsState}
        insights={reviewInsights}
        searchQuery={search}
        onAccept={(id) => {
          setInsights((cur) =>
            cur.map((i) =>
              i.id === id ? { ...i, lifecycle: "active" } : i,
            ),
          );
          if (typeof console !== "undefined") {
            console.info("[memory] accept insight", id);
          }
        }}
        onReject={(insight) => setRejectTarget(insight)}
        onOpenSource={(insight) => setSourceTarget(insight)}
        onRetry={() => {
          if (typeof console !== "undefined") {
            console.info("[memory] retry insights fetch");
          }
        }}
      />

      <SectionHeader
        id="blocklist"
        title={`Blocklist (${blocklist.length})`}
        subtitle="거절한 토픽의 차단 만료/사유 — lVcRk Blocklist mirror."
      />
      <BlocklistTable
        entries={blocklist}
        onUnblock={(topicKey) => {
          setBlocklist((cur) => cur.filter((b) => b.topicKey !== topicKey));
          if (typeof console !== "undefined") {
            console.info("[memory] unblock", topicKey);
          }
        }}
      />

      <RejectConfirmModal
        target={rejectTarget}
        onClose={() => setRejectTarget(null)}
        onConfirm={(input) => {
          handleReject(input);
          setRejectTarget(null);
        }}
      />

      <SourceDrawer
        insight={sourceTarget}
        messages={
          sourceTarget ? snapshot.sources[sourceTarget.id] ?? [] : []
        }
        onClose={() => setSourceTarget(null)}
      />

      <DryRunPreviewModal
        open={dryRunOpen}
        onClose={() => setDryRunOpen(false)}
        preview={snapshot.dryRun}
        onApply={() => {
          if (typeof console !== "undefined") {
            console.info(
              "[memory] dry-run apply",
              snapshot.dryRun.changes.length,
            );
          }
          setDryRunOpen(false);
        }}
      />
    </section>
  );

  function handleReject(input: RejectConfirmInput) {
    // 1) 인사이트를 큐에서 제거 (lifecycle 변경 대신 archive 로 이동).
    setInsights((cur) =>
      cur.map((i) =>
        i.id === input.insightId ? { ...i, lifecycle: "archive" } : i,
      ),
    );
    // 2) 블록리스트에 추가 (이미 있으면 갱신).
    const expiresAt =
      input.duration === "forever"
        ? null
        : new Date(
            Date.now() +
              (input.duration === "7d" ? 7 : 30) * 24 * 60 * 60 * 1000,
          ).toISOString();
    setBlocklist((cur) => {
      const existingIdx = cur.findIndex(
        (b) => b.topicKey === input.topicKey,
      );
      const entry: BlocklistEntry = {
        topicKey: input.topicKey,
        topic: input.topicKey,
        reason: input.reason,
        blockedAt: new Date().toISOString(),
        expiresAt,
      };
      if (existingIdx >= 0) {
        const next = [...cur];
        next[existingIdx] = entry;
        return next;
      }
      return [entry, ...cur];
    });
    if (typeof console !== "undefined") {
      console.info(
        "[memory] reject + block",
        input.insightId,
        input.duration,
      );
    }
  }
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
    <header
      className="flex flex-col gap-0.5"
      id={id}
      data-testid={`section-${id}`}
    >
      <h2 className="text-base font-semibold text-(--foreground-strong)">
        {title}
      </h2>
      <p className="text-xs text-(--muted-foreground)">{subtitle}</p>
    </header>
  );
}

function DreamingStatusLine({
  lastDreaming,
  ...rest
}: {
  lastDreaming: ReturnType<typeof getMemorySnapshot>["lastDreaming"];
  "data-testid"?: string;
}) {
  if (!lastDreaming) return null;
  const tone =
    lastDreaming.status === "success"
      ? "success"
      : lastDreaming.status === "skip"
        ? "warning"
        : "danger";
  return (
    <div
      data-testid={rest["data-testid"]}
      className="mt-1 flex flex-wrap items-center gap-2 text-xs text-(--muted-foreground)"
    >
      <span>마지막 사이클</span>
      <Badge tone={tone}>{lastDreaming.status}</Badge>
      <span>
        · 인사이트 {lastDreaming.generatedCount}건 ·{" "}
        {new Date(lastDreaming.endedAt).toLocaleString("ko-KR")}
      </span>
    </div>
  );
}

/** ?insights=foo / ?projects=foo 의 4-variant 매핑 — 알 수 없으면 default. */
function readState<T extends string>(
  raw: string | null,
  valid: readonly T[],
): T {
  if (raw && (valid as readonly string[]).includes(raw)) {
    return raw as T;
  }
  return "default" as T;
}

interface InsightFilterable {
  topic: string;
  text: string;
}

function applyFilter<T extends InsightFilterable>(
  items: readonly T[],
  query: string,
): T[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...items];
  return items.filter((item) => {
    if (item.topic.toLowerCase().includes(q)) return true;
    if (item.text.toLowerCase().includes(q)) return true;
    return false;
  });
}

interface Counts {
  review: number;
  active: number;
  blocked: number;
}

function summarizeCounts(
  insights: readonly MemoryInsight[],
  blocklist: readonly BlocklistEntry[],
): Counts {
  let review = 0;
  let active = 0;
  for (const i of insights) {
    if (i.lifecycle === "review") review += 1;
    else if (i.lifecycle === "active") active += 1;
  }
  return { review, active, blocked: blocklist.length };
}
