"use client";

/**
 * /memory/insights — BIZ-92 / BIZ-90 Insights · Dreaming 라이프사이클 화면.
 *
 * 4-탭 검토 UX:
 *  - Review   : suggestions 큐(BIZ-79) — accept/edit/reject 액션을 InsightCard 로 노출.
 *  - Active   : 채택되어 USER.md 에 살아있는 인사이트 (InsightStore archived_at IS NULL).
 *  - Archive  : 사용자 삭제 등으로 sidecar 에서 archive 된 인사이트.
 *  - Blocklist: 재학습이 차단된 토픽 — 정규형 + 사유 + 차단 시각.
 *
 * 상단:
 *  - 타이틀 + last_run 메타(있으면) + Dry-run Preview / Run Dreaming Now 버튼.
 *  - Dry-run Preview / Run Dreaming Now 는 BIZ-95 sub-issue 의 모달 진입점 stub.
 *
 * 필터 행 (Review/Active/Archive 공통):
 *  - 토픽/본문 검색, Confidence ≥ slider, Source 채널 multi-select,
 *    "cron/recipe 노이즈 자동 다운(0.3×)" 정보 칩.
 *  - Blocklist 탭에서는 confidence/cron-noise 비활성 — 정책상 의미 없음.
 *
 * 비고:
 *  - 인라인 편집 / Reject Confirm 정교화 / Source Drawer / Dry-run Preview Drawer 는
 *    각각 BIZ-93, BIZ-94, BIZ-95 sub-issue 의 책임. 본 페이지에서는 모달/드로어를
 *    stub 으로만 두고 페이지의 골격만 완성한다.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Eye,
  Loader2,
  PlayCircle,
  RefreshCw,
  Sparkles,
  Wand2,
} from "lucide-react";
import { Badge } from "@/components/atoms/Badge";
import { Button } from "@/components/atoms/Button";
import { Tabs, type TabItem } from "@/components/atoms/Tabs";
import { Modal } from "@/components/primitives/Modal";
import { ConfirmGate } from "@/components/primitives/ConfirmGate";
import { useToast } from "@/components/primitives/Toast";
import { InsightCard, InsightCardEmpty } from "@/components/domain/InsightCard";
import {
  DEFAULT_INSIGHT_FILTER,
  InsightFilters,
  type InsightFilterValue,
} from "@/components/domain/InsightFilters";
import {
  type Suggestion,
  acceptSuggestion,
  editSuggestion,
  getSuggestionSources,
  listSuggestions,
  rejectSuggestion,
  type SuggestionSourceMessage,
} from "@/lib/api/suggestions";
import {
  type BlocklistEntry,
  type InsightItem,
  listBlocklist,
  listInsights,
} from "@/lib/api/insights";
import { getDreamingStatusV2 } from "@/lib/api/dreaming-runs";
import type { DreamingStatusResponse } from "@/lib/api/dreaming-runs";
import { cn } from "@/lib/cn";

type TabValue = "review" | "active" | "archive" | "blocklist";

export default function MemoryInsightsPage() {
  const { push: pushToast } = useToast();

  const [tab, setTab] = useState<TabValue>("review");
  const [filter, setFilter] = useState<InsightFilterValue>(DEFAULT_INSIGHT_FILTER);

  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [pendingCount, setPendingCount] = useState(0);
  const [activeInsights, setActiveInsights] = useState<InsightItem[]>([]);
  const [archivedInsights, setArchivedInsights] = useState<InsightItem[]>([]);
  const [activeCount, setActiveCount] = useState(0);
  const [archivedCount, setArchivedCount] = useState(0);
  const [blocklist, setBlocklist] = useState<BlocklistEntry[]>([]);
  const [dreamingStatus, setDreamingStatus] =
    useState<DreamingStatusResponse | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Edit / Reject / Sources 모달
  const [editTarget, setEditTarget] = useState<Suggestion | null>(null);
  const [rejectTarget, setRejectTarget] = useState<Suggestion | null>(null);
  const [sourceViewer, setSourceViewer] = useState<{
    suggestion: Suggestion;
    sources: SuggestionSourceMessage[] | null;
    loading: boolean;
    error: string | null;
  } | null>(null);
  const [dryRunOpen, setDryRunOpen] = useState(false);

  // ---------------------------------------------------------------------
  // 데이터 로드 — 한 번에 병렬 호출. 일부 503 은 무시하고 카운트 0 으로 둔다.
  // ---------------------------------------------------------------------

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // 병렬 호출 — 한 엔드포인트가 503 이어도 다른 탭은 사용 가능해야 함.
      const [suggRes, activeRes, archivedRes, blockRes, statusRes] =
        await Promise.allSettled([
          listSuggestions("pending"),
          listInsights("active"),
          listInsights("archived"),
          listBlocklist(),
          getDreamingStatusV2(),
        ]);

      if (suggRes.status === "fulfilled") {
        setSuggestions(suggRes.value.suggestions);
        setPendingCount(suggRes.value.pending_count);
      } else {
        setSuggestions([]);
        setPendingCount(0);
      }

      if (activeRes.status === "fulfilled") {
        setActiveInsights(activeRes.value.insights);
        setActiveCount(activeRes.value.active_count);
      } else {
        setActiveInsights([]);
        setActiveCount(0);
      }

      if (archivedRes.status === "fulfilled") {
        setArchivedInsights(archivedRes.value.insights);
        setArchivedCount(archivedRes.value.archived_count);
      } else {
        setArchivedInsights([]);
        setArchivedCount(0);
      }

      if (blockRes.status === "fulfilled") {
        setBlocklist(blockRes.value.entries);
      } else {
        setBlocklist([]);
      }

      if (statusRes.status === "fulfilled") {
        setDreamingStatus(statusRes.value);
      } else {
        setDreamingStatus(null);
      }

      // 모든 호출이 거절됐다면 명시적 에러를 노출.
      if (
        [suggRes, activeRes, archivedRes, blockRes].every(
          (r) => r.status === "rejected",
        )
      ) {
        setError("인사이트 데이터를 불러오지 못했어요.");
      }
    } catch (e) {
      // Promise.allSettled 는 throw 하지 않지만 안전망.
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // ---------------------------------------------------------------------
  // 채널 리스트 — Review 탭에서는 suggestions 가 channel 정보를 직접 갖지 않아
  // 빈 배열. Active/Archive 탭의 source_msg_ids 도 channel 미포함이라 우선 [].
  // (BIZ-94 Source Drawer 가 채널을 노출하면 거기서 추출).
  // ---------------------------------------------------------------------
  const availableChannels: string[] = useMemo(() => [], []);

  // ---------------------------------------------------------------------
  // 탭별 필터 적용
  // ---------------------------------------------------------------------

  const filteredSuggestions = useMemo<Suggestion[]>(() => {
    return applyFilter(suggestions, filter, (s) => ({
      topic: s.topic,
      text: s.text,
      confidence: s.confidence,
    }));
  }, [suggestions, filter]);

  const filteredActive = useMemo<InsightItem[]>(() => {
    return applyFilter(activeInsights, filter, (i) => ({
      topic: i.topic,
      text: i.text,
      confidence: i.confidence,
    }));
  }, [activeInsights, filter]);

  const filteredArchive = useMemo<InsightItem[]>(() => {
    return applyFilter(archivedInsights, filter, (i) => ({
      topic: i.topic,
      text: i.text,
      confidence: i.confidence,
    }));
  }, [archivedInsights, filter]);

  const filteredBlocklist = useMemo<BlocklistEntry[]>(() => {
    const q = filter.query.trim().toLowerCase();
    if (!q) return blocklist;
    return blocklist.filter((b) =>
      `${b.topic} ${b.reason}`.toLowerCase().includes(q),
    );
  }, [blocklist, filter.query]);

  // ---------------------------------------------------------------------
  // mutations — Review 탭 액션
  // ---------------------------------------------------------------------

  const dreamingRunning =
    !!dreamingStatus?.last_run && dreamingStatus.last_run.ended_at === null;

  const handleAccept = async (s: Suggestion) => {
    try {
      await acceptSuggestion(s.id);
      pushToast({
        tone: "success",
        title: "제안을 USER.md 에 적용했어요.",
        description: truncate(s.text, 80),
      });
      await refresh();
    } catch (e) {
      pushToast({
        tone: "destructive-soft",
        title: "적용에 실패했어요.",
        description: e instanceof Error ? e.message : String(e),
      });
    }
  };

  const handleConfirmReject = async (reason: string) => {
    if (!rejectTarget) return;
    try {
      await rejectSuggestion(rejectTarget.id, reason);
      pushToast({
        tone: "info",
        title: "제안을 거절하고 블록리스트에 추가했어요.",
        description: `다음 드리밍부터 “${rejectTarget.topic}” 주제는 다시 추출되지 않아요.`,
      });
      setRejectTarget(null);
      await refresh();
    } catch (e) {
      pushToast({
        tone: "destructive-soft",
        title: "거절 처리에 실패했어요.",
        description: e instanceof Error ? e.message : String(e),
      });
    }
  };

  const handleEditSave = async (s: Suggestion, text: string) => {
    try {
      await editSuggestion(s.id, text);
      pushToast({
        tone: "success",
        title: "수정한 제안을 USER.md 에 적용했어요.",
      });
      setEditTarget(null);
      await refresh();
    } catch (e) {
      pushToast({
        tone: "destructive-soft",
        title: "수정 적용에 실패했어요.",
        description: e instanceof Error ? e.message : String(e),
      });
    }
  };

  const handleOpenSources = async (s: Suggestion) => {
    setSourceViewer({ suggestion: s, sources: null, loading: true, error: null });
    try {
      const res = await getSuggestionSources(s.id);
      setSourceViewer({
        suggestion: res.suggestion,
        sources: res.sources,
        loading: false,
        error: null,
      });
    } catch (e) {
      setSourceViewer((prev) =>
        prev
          ? {
              ...prev,
              loading: false,
              error: e instanceof Error ? e.message : String(e),
            }
          : prev,
      );
    }
  };

  // ---------------------------------------------------------------------
  // render
  // ---------------------------------------------------------------------

  const tabs: ReadonlyArray<TabItem<TabValue>> = [
    { value: "review", label: "Review", count: pendingCount },
    { value: "active", label: "Active", count: activeCount },
    { value: "archive", label: "Archive", count: archivedCount },
    { value: "blocklist", label: "Blocklist", count: blocklist.length },
  ];

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-5">
      {/* 1. 상단 — 타이틀 + last_run 메타 + 액션 */}
      <header className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold text-(--foreground-strong)">
            <Sparkles size={18} aria-hidden /> Insights · Dreaming 라이프사이클
          </h1>
          <p className="mt-1 text-sm text-(--muted-foreground)">
            드리밍이 추출한 인사이트를 검토·관리하고, 채택/보관/차단 흐름을 한
            화면에서 봅니다.
          </p>
          <DreamingStatusLine status={dreamingStatus} />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="md"
            onClick={() => setDryRunOpen(true)}
            leftIcon={<Eye size={14} aria-hidden />}
          >
            Dry-run Preview
          </Button>
          <Button
            variant="primary"
            size="md"
            disabled={dreamingRunning}
            leftIcon={
              dreamingRunning ? (
                <Loader2 size={14} aria-hidden className="animate-spin" />
              ) : (
                <PlayCircle size={14} aria-hidden />
              )
            }
            onClick={() => {
              // BIZ-95 Run Dreaming Now — 즉시 트리거. 본 PR 에서는 토스트만.
              pushToast({
                tone: "info",
                title: "Run Dreaming Now 는 곧 연결됩니다.",
                description: "BIZ-95 에서 진행 모달 + 트리거 호출이 추가될 예정.",
              });
            }}
          >
            {dreamingRunning ? "드리밍 진행 중…" : "Run Dreaming Now"}
          </Button>
          <Button
            variant="ghost"
            size="md"
            onClick={() => void refresh()}
            leftIcon={
              loading ? (
                <Loader2 size={14} aria-hidden className="animate-spin" />
              ) : (
                <RefreshCw size={14} aria-hidden />
              )
            }
            aria-label="새로고침"
            disabled={loading}
          >
            새로고침
          </Button>
        </div>
      </header>

      {/* 2. 탭 */}
      <Tabs<TabValue>
        items={tabs}
        value={tab}
        onValueChange={setTab}
        ariaLabel="Insights 탭"
      />

      {/* 3. 필터 */}
      <InsightFilters
        value={filter}
        onChange={setFilter}
        availableChannels={availableChannels}
        hideConfidence={tab === "blocklist"}
        hideNoiseHint={tab === "blocklist"}
      />

      {/* 4. 본문 */}
      {error ? (
        <div className="rounded-(--radius-m) border border-(--color-error) bg-(--color-error-bg) px-4 py-3 text-sm text-(--color-error)">
          {error}
        </div>
      ) : loading && suggestions.length === 0 && activeInsights.length === 0 ? (
        <InsightCardEmpty>불러오는 중…</InsightCardEmpty>
      ) : (
        <section
          aria-live="polite"
          className={cn(
            "flex flex-col gap-2 rounded-(--radius-l) border border-(--border) bg-(--card) p-4",
          )}
        >
          {tab === "review" ? (
            filteredSuggestions.length === 0 ? (
              <InsightCardEmpty>
                {suggestions.length === 0
                  ? "검토할 제안이 없어요. 드리밍을 한 번 돌려볼까요?"
                  : "필터에 해당하는 제안이 없어요."}
              </InsightCardEmpty>
            ) : (
              filteredSuggestions.map((s) => (
                <InsightCard
                  key={s.id}
                  variant="review"
                  data={{
                    topic: s.topic,
                    text: s.text,
                    confidence: s.confidence,
                    evidenceCount: s.evidence_count,
                    timestamp: s.updated_at,
                    timestampLabel: "업데이트",
                  }}
                  rejectOnly={isCronNoise(s)}
                  disabled={dreamingRunning}
                  onOpenSources={() => void handleOpenSources(s)}
                  onAccept={() => void handleAccept(s)}
                  onEdit={() => setEditTarget(s)}
                  onReject={() => setRejectTarget(s)}
                  onDefer={() => {
                    // BIZ-93 Defer — 본 PR 에서는 토스트만 안내.
                    pushToast({
                      tone: "info",
                      title: "보류는 곧 연결됩니다.",
                      description:
                        "BIZ-93 에서 Defer(다음 드리밍까지 큐 유지) 가 추가될 예정.",
                    });
                  }}
                />
              ))
            )
          ) : tab === "active" ? (
            filteredActive.length === 0 ? (
              <InsightCardEmpty>
                {activeInsights.length === 0
                  ? "USER.md 에 아직 채택된 인사이트가 없어요."
                  : "필터에 해당하는 인사이트가 없어요."}
              </InsightCardEmpty>
            ) : (
              filteredActive.map((i) => (
                <InsightCard
                  key={`${i.topic}-${i.first_seen}`}
                  variant="read"
                  data={{
                    topic: i.topic,
                    text: i.text,
                    confidence: i.confidence,
                    evidenceCount: i.evidence_count,
                    timestamp: i.last_seen,
                    timestampLabel: "마지막 관찰",
                  }}
                />
              ))
            )
          ) : tab === "archive" ? (
            filteredArchive.length === 0 ? (
              <InsightCardEmpty>
                {archivedInsights.length === 0
                  ? "보관된 인사이트가 없어요."
                  : "필터에 해당하는 인사이트가 없어요."}
              </InsightCardEmpty>
            ) : (
              filteredArchive.map((i) => (
                <InsightCard
                  key={`${i.topic}-${i.first_seen}`}
                  variant="read"
                  data={{
                    topic: i.topic,
                    text: i.text,
                    confidence: i.confidence,
                    evidenceCount: i.evidence_count,
                    timestamp: i.archived_at,
                    timestampLabel: "보관",
                  }}
                />
              ))
            )
          ) : (
            // blocklist
            filteredBlocklist.length === 0 ? (
              <InsightCardEmpty>
                {blocklist.length === 0
                  ? "차단된 토픽이 없어요."
                  : "필터에 해당하는 차단 토픽이 없어요."}
              </InsightCardEmpty>
            ) : (
              filteredBlocklist.map((b) => (
                <InsightCard
                  key={b.topic_key}
                  variant="read"
                  data={{
                    topic: b.topic,
                    text: b.reason || "사유 미기재",
                    confidence: null,
                    evidenceCount: null,
                    timestamp: b.blocked_at,
                    timestampLabel: "차단",
                    extraMeta: <>· key: {b.topic_key}</>,
                  }}
                />
              ))
            )
          )}
        </section>
      )}

      {/* Sources 모달 — Review 탭 한정 */}
      <Modal
        open={!!sourceViewer}
        onOpenChange={(o) => {
          if (!o) setSourceViewer(null);
        }}
        title="제안의 근거 메시지"
        description={
          sourceViewer
            ? `${sourceViewer.suggestion.topic} · ${sourceViewer.suggestion.source_msg_ids.length}건`
            : undefined
        }
        size="lg"
      >
        {sourceViewer?.loading ? (
          <div className="grid place-items-center py-8 text-xs text-(--muted-foreground)">
            <Loader2 size={16} aria-hidden className="animate-spin" />
          </div>
        ) : sourceViewer?.error ? (
          <div className="rounded-(--radius-m) border border-(--color-error) bg-(--color-error-bg) px-3 py-2 text-xs text-(--color-error)">
            근거 메시지를 불러오지 못했어요: {sourceViewer.error}
          </div>
        ) : sourceViewer && sourceViewer.sources ? (
          sourceViewer.sources.length === 0 ? (
            <div className="rounded-(--radius-m) border border-dashed border-(--border) bg-(--surface) px-3 py-6 text-center text-xs text-(--muted-foreground)">
              근거 메시지를 찾을 수 없어요. 대화가 아카이브된 상태일 수 있어요.
            </div>
          ) : (
            <ol className="flex flex-col gap-2">
              {sourceViewer.sources.map((m) => (
                <li
                  key={m.id}
                  className="rounded-(--radius-m) border border-(--border) bg-(--surface) px-3 py-2"
                >
                  <div className="flex items-center justify-between text-[10px] font-mono text-(--muted-foreground)">
                    <span>
                      {m.role}
                      {m.channel ? ` · ${m.channel}` : ""}
                    </span>
                    <span>
                      {new Date(m.timestamp).toLocaleString("ko-KR")}
                    </span>
                  </div>
                  <p className="mt-1 whitespace-pre-wrap break-words text-sm text-(--foreground)">
                    {m.content}
                  </p>
                </li>
              ))}
            </ol>
          )
        ) : null}
      </Modal>

      {/* Edit 모달 — 인라인 편집의 임시 stub. */}
      <EditModal
        target={editTarget}
        onCancel={() => setEditTarget(null)}
        onSave={(text) =>
          editTarget ? handleEditSave(editTarget, text) : Promise.resolve()
        }
      />

      {/* Reject 확인 게이트 */}
      <RejectConfirm
        target={rejectTarget}
        onCancel={() => setRejectTarget(null)}
        onConfirm={handleConfirmReject}
      />

      {/* Dry-run Preview 드로어 stub — BIZ-95 */}
      <Modal
        open={dryRunOpen}
        onOpenChange={setDryRunOpen}
        title="Dry-run Preview"
        description="드리밍이 추출할 인사이트를 미리 봅니다 (실제 적용 없음)."
        size="md"
      >
        <div className="rounded-(--radius-m) border border-dashed border-(--border) bg-(--surface) px-3 py-6 text-center text-xs text-(--muted-foreground)">
          BIZ-95 sub-issue 에서 dry-run 결과 시뮬레이션이 추가될 예정이에요.
        </div>
      </Modal>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 보조 컴포넌트
// ---------------------------------------------------------------------------

function DreamingStatusLine({
  status,
}: {
  status: DreamingStatusResponse | null;
}) {
  if (!status) return null;
  const last = status.last_run;
  const next = status.next_run;
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-(--muted-foreground)">
      <Wand2 size={12} aria-hidden />
      <span>
        마지막 사이클:{" "}
        {last ? (
          <>
            <Badge
              tone={
                last.status === "success"
                  ? "success"
                  : last.status === "error"
                    ? "danger"
                    : last.status === "skip"
                      ? "warning"
                      : "info"
              }
            >
              {last.status}
            </Badge>{" "}
            {last.ended_at
              ? new Date(last.ended_at).toLocaleString("ko-KR")
              : "진행 중"}{" "}
            · 인사이트 {last.generated_insight_count}건
          </>
        ) : (
          "기록 없음"
        )}
      </span>
      {next ? (
        <span>· 다음 예정: {new Date(next).toLocaleString("ko-KR")}</span>
      ) : null}
    </div>
  );
}

interface EditModalProps {
  target: Suggestion | null;
  onCancel: () => void;
  onSave: (text: string) => Promise<void>;
}

function EditModal({ target, onCancel, onSave }: EditModalProps) {
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDraft(target?.text ?? "");
  }, [target?.id, target?.text]);

  return (
    <Modal
      open={!!target}
      onOpenChange={(o) => {
        if (!o) onCancel();
      }}
      title="제안 편집"
      description={target ? `${target.topic} · ${target.id}` : undefined}
      size="md"
      footer={
        <div className="flex items-center justify-end gap-2">
          <Button
            variant="ghost"
            size="md"
            onClick={onCancel}
            disabled={saving}
          >
            취소
          </Button>
          <Button
            variant="primary"
            size="md"
            disabled={
              saving || draft.trim() === "" || draft.trim() === target?.text
            }
            onClick={async () => {
              setSaving(true);
              try {
                await onSave(draft.trim());
              } finally {
                setSaving(false);
              }
            }}
            leftIcon={
              saving ? (
                <Loader2 size={12} aria-hidden className="animate-spin" />
              ) : null
            }
          >
            {saving ? "적용 중…" : "수정 적용"}
          </Button>
        </div>
      }
    >
      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        rows={5}
        spellCheck={false}
        className="w-full resize-y rounded-(--radius-m) border border-(--border) bg-(--card) p-2 text-sm leading-6 text-(--foreground) outline-none focus:border-(--primary)"
        aria-label={`제안 편집 — ${target?.id ?? ""}`}
      />
    </Modal>
  );
}

interface RejectConfirmProps {
  target: Suggestion | null;
  onCancel: () => void;
  onConfirm: (reason: string) => void | Promise<void>;
}

function RejectConfirm({ target, onCancel, onConfirm }: RejectConfirmProps) {
  const [reason, setReason] = useState("");
  useEffect(() => {
    setReason("");
  }, [target?.id]);

  return (
    <ConfirmGate
      open={!!target}
      onOpenChange={(o) => {
        if (!o) onCancel();
      }}
      title="이 제안을 거절할까요?"
      description={
        <>
          거절하면 <span className="font-mono">{target?.topic}</span> 주제는 다음
          드리밍 사이클부터 블록리스트에 의해 자동으로 차단됩니다. 다시 허용하려면
          블록리스트에서 직접 제거해야 해요. 계속하려면{" "}
          <span className="font-mono">REJECT</span>를 입력해 주세요.
        </>
      }
      confirmation="REJECT"
      confirmLabel="거절하고 블록"
      onConfirm={() => onConfirm(reason)}
    >
      <div className="flex flex-col gap-2">
        {target ? (
          <div className="rounded-(--radius-m) border border-(--border) bg-(--surface) px-3 py-2 text-xs">
            <div className="font-mono text-[10px] text-(--muted-foreground)">
              {target.topic} · {target.id}
            </div>
            <div className="mt-1 break-words text-(--foreground)">
              {target.text}
            </div>
          </div>
        ) : null}
        <label className="flex flex-col gap-1 text-xs text-(--muted-foreground)">
          <span>거절 사유 (선택, audit 로그에 남아요)</span>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={2}
            className="w-full resize-y rounded-(--radius-m) border border-(--border) bg-(--card) p-2 text-sm text-(--foreground) outline-none focus:border-(--primary)"
            placeholder="예: 일회성 농담이라 학습할 가치 없음"
          />
        </label>
      </div>
    </ConfirmGate>
  );
}

// ---------------------------------------------------------------------------
// 보조 함수
// ---------------------------------------------------------------------------

/**
 * 공통 필터 — query / minConfidence 만 적용. 채널 필터는 데이터에 channel 정보가
 * 들어오면 활성화한다 (현재 API 응답에는 channel 필드가 없다).
 */
function applyFilter<T>(
  items: T[],
  filter: InsightFilterValue,
  pick: (item: T) => { topic: string; text: string; confidence: number },
): T[] {
  const q = filter.query.trim().toLowerCase();
  return items.filter((item) => {
    const { topic, text, confidence } = pick(item);
    if (filter.minConfidence > 0 && confidence < filter.minConfidence) {
      return false;
    }
    if (q && !`${topic} ${text}`.toLowerCase().includes(q)) {
      return false;
    }
    return true;
  });
}

/**
 * cron/recipe 노이즈 휴리스틱 — topic 또는 본문에 "cron" / "recipe" / "schedule"
 * 키워드가 있고 confidence 가 0.4 미만(=low) 인 경우. 백엔드 라벨이 들어오기
 * 전까지의 임시 분류. BIZ-90 의 "low-conf+cron-derived" 정의 참고.
 */
function isCronNoise(s: Suggestion): boolean {
  if (s.confidence >= 0.4) return false;
  const haystack = `${s.topic} ${s.text}`.toLowerCase();
  return /\b(cron|recipe|schedule|스케줄|자동\s*실행)\b/.test(haystack);
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}
