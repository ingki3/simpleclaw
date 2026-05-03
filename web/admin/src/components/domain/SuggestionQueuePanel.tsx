"use client";

/**
 * SuggestionQueuePanel — Memory 화면(BIZ-79) 의 검토 큐 카드.
 *
 * 책임:
 *  - pending suggestions 를 한 행씩 보여주고 accept / edit / reject / 근거보기 액션 제공.
 *  - edit 은 인라인 textarea 로 전환되어 저장 시 ``editSuggestion``을 호출.
 *  - reject 는 ConfirmGate 로 가드 — 블록리스트 추가 = "재추출 차단"이라는 단방향 효과 안내.
 *  - "근거 보기"는 Modal 로 ``getSuggestionSources`` 결과를 시간순 표시.
 *
 * 비책임:
 *  - 패널 자체에 polling 은 없다 — 외부에서 ``onChanged`` 콜백을 받아 호출자 측 refresh 한다.
 *    드리밍 진행 중에는 ``disabled``로 액션을 차단(상위 페이지가 결정).
 *
 * 디자인 결정:
 *  - confidence/evidence_count 는 작은 메타 라인으로 노출 — 시각적으로 본문(text)을
 *    가리지 않도록 한다. auto-promote 임계값과 비교하는 색상 강조는 향후 추가 가능
 *    (현재 응답에는 임계값이 포함되지 않음 → 미장식).
 *  - empty state: pending 이 0건이면 안내 카피 + 새로고침 버튼만 노출.
 */

import { useCallback, useEffect, useState } from "react";
import {
  Check,
  Eye,
  Loader2,
  Pencil,
  RefreshCw,
  Save,
  Sparkles,
  X,
} from "lucide-react";
import { Badge } from "@/components/atoms/Badge";
import { Button } from "@/components/atoms/Button";
import { Modal } from "@/components/primitives/Modal";
import { ConfirmGate } from "@/components/primitives/ConfirmGate";
import { useToast } from "@/components/primitives/Toast";
import {
  acceptSuggestion,
  editSuggestion,
  getSuggestionSources,
  listSuggestions,
  rejectSuggestion,
  type Suggestion,
  type SuggestionSourceMessage,
} from "@/lib/api/suggestions";
import { cn } from "@/lib/cn";

export interface SuggestionQueuePanelProps {
  /** 외부(드리밍 진행 중 등)에서 액션을 차단해야 할 때 true. */
  disabled?: boolean;
  /** accept/edit/reject 가 성공한 직후 호출 — 상위에서 MEMORY.md 인덱스를 재로드한다. */
  onChanged?: () => void;
}

/**
 * 메인 카드. 자체 데이터 fetch + 리스트 렌더 + 액션 핸들러를 모두 포함한다.
 * 데이터 갱신은 ``refresh`` 1개의 진입점으로 단순화 — 액션 후엔 항상 재로드.
 */
export function SuggestionQueuePanel({
  disabled,
  onChanged,
}: SuggestionQueuePanelProps) {
  const { push: pushToast } = useToast();

  const [items, setItems] = useState<Suggestion[]>([]);
  const [pendingCount, setPendingCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Source viewer modal 상태 — 한 번에 하나의 suggestion 만 노출.
  const [sourceViewer, setSourceViewer] = useState<{
    suggestion: Suggestion;
    sources: SuggestionSourceMessage[] | null;
    loading: boolean;
    error: string | null;
  } | null>(null);

  // Reject confirm 게이트 — 블록리스트 영구 효과를 한 번 더 확인.
  const [rejectTarget, setRejectTarget] = useState<Suggestion | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listSuggestions("pending");
      setItems(res.suggestions);
      setPendingCount(res.pending_count);
    } catch (e) {
      // 503(서버에 큐 미설정)은 운영 환경 차이 — 카드 본문에 안내만 띄운다.
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // ---------------------------------------------------------------------
  // 액션 핸들러 — 성공 토스트 + 큐 재로드 + 외부 onChanged 호출.
  // ---------------------------------------------------------------------

  const handleAccept = async (s: Suggestion) => {
    try {
      await acceptSuggestion(s.id);
      pushToast({
        tone: "success",
        title: "제안을 USER.md 에 적용했어요.",
        description: truncate(s.text, 80),
      });
      await refresh();
      onChanged?.();
    } catch (e) {
      pushToast({
        tone: "destructive-soft",
        title: "적용에 실패했어요.",
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
      await refresh();
      onChanged?.();
    } catch (e) {
      pushToast({
        tone: "destructive-soft",
        title: "수정 적용에 실패했어요.",
        description: e instanceof Error ? e.message : String(e),
      });
      throw e;
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
      onChanged?.();
    } catch (e) {
      pushToast({
        tone: "destructive-soft",
        title: "거절 처리에 실패했어요.",
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

  return (
    <section
      aria-labelledby="suggestion-queue-title"
      className="flex flex-col gap-3 rounded-[--radius-l] border border-[--border] bg-[--card] p-5"
    >
      <header className="flex items-center justify-between gap-2">
        <div>
          <h2
            id="suggestion-queue-title"
            className="flex items-center gap-2 text-sm font-semibold text-[--foreground-strong]"
          >
            <Sparkles size={14} aria-hidden /> 검토 대기 큐
            <Badge tone={pendingCount > 0 ? "brand" : "neutral"}>
              {pendingCount}
            </Badge>
          </h2>
          <p className="mt-1 text-xs text-[--muted-foreground]">
            드리밍이 추출했지만 임계값에 못 미친 항목이에요. 적용·수정·거절 후 USER.md 에 반영됩니다.
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => void refresh()}
          leftIcon={
            loading ? (
              <Loader2 size={12} aria-hidden className="animate-spin" />
            ) : (
              <RefreshCw size={12} aria-hidden />
            )
          }
          aria-label="검토 큐 새로고침"
          disabled={loading}
        >
          새로고침
        </Button>
      </header>

      {error ? (
        <div className="rounded-[--radius-m] border border-[--color-error] bg-[--color-error-bg] px-3 py-2 text-xs text-[--color-error]">
          큐를 불러오지 못했어요: {error}
        </div>
      ) : loading && items.length === 0 ? (
        <div className="rounded-[--radius-m] border border-dashed border-[--border] bg-[--surface] px-3 py-6 text-center text-xs text-[--muted-foreground]">
          불러오는 중…
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-[--radius-m] border border-dashed border-[--border] bg-[--surface] px-3 py-6 text-center text-xs text-[--muted-foreground]">
          검토할 제안이 없어요. 드리밍을 한 번 돌려볼까요?
        </div>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((s) => (
            <SuggestionRow
              key={s.id}
              suggestion={s}
              disabled={disabled}
              onAccept={() => handleAccept(s)}
              onEditSave={(text) => handleEditSave(s, text)}
              onRequestReject={() => setRejectTarget(s)}
              onOpenSources={() => handleOpenSources(s)}
            />
          ))}
        </ul>
      )}

      {/* 근거 메시지 모달 */}
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
          <div className="grid place-items-center py-8 text-xs text-[--muted-foreground]">
            <Loader2 size={16} aria-hidden className="animate-spin" />
          </div>
        ) : sourceViewer?.error ? (
          <div className="rounded-[--radius-m] border border-[--color-error] bg-[--color-error-bg] px-3 py-2 text-xs text-[--color-error]">
            근거 메시지를 불러오지 못했어요: {sourceViewer.error}
          </div>
        ) : sourceViewer && sourceViewer.sources ? (
          sourceViewer.sources.length === 0 ? (
            <div className="rounded-[--radius-m] border border-dashed border-[--border] bg-[--surface] px-3 py-6 text-center text-xs text-[--muted-foreground]">
              근거 메시지를 찾을 수 없어요. 대화가 아카이브된 상태일 수 있어요.
            </div>
          ) : (
            <ol className="flex flex-col gap-2">
              {sourceViewer.sources.map((m) => (
                <li
                  key={m.id}
                  className="rounded-[--radius-m] border border-[--border] bg-[--surface] px-3 py-2"
                >
                  <div className="flex items-center justify-between text-[10px] font-mono text-[--muted-foreground]">
                    <span>
                      {m.role}
                      {m.channel ? ` · ${m.channel}` : ""}
                    </span>
                    <span>{formatTimestamp(m.timestamp)}</span>
                  </div>
                  <p className="mt-1 whitespace-pre-wrap break-words text-sm text-[--foreground]">
                    {m.content}
                  </p>
                </li>
              ))}
            </ol>
          )
        ) : null}
      </Modal>

      {/* Reject 확인 게이트 — reason 자유서술. */}
      <RejectConfirm
        target={rejectTarget}
        onCancel={() => setRejectTarget(null)}
        onConfirm={handleConfirmReject}
      />
    </section>
  );
}

// ---------------------------------------------------------------------------
// 1행 — 인라인 편집 토글을 자체 상태로 관리한다.
// ---------------------------------------------------------------------------

interface SuggestionRowProps {
  suggestion: Suggestion;
  disabled?: boolean;
  onAccept: () => void | Promise<void>;
  onEditSave: (text: string) => Promise<void>;
  onRequestReject: () => void;
  onOpenSources: () => void;
}

function SuggestionRow({
  suggestion,
  disabled,
  onAccept,
  onEditSave,
  onRequestReject,
  onOpenSources,
}: SuggestionRowProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(suggestion.text);
  const [saving, setSaving] = useState(false);
  const [accepting, setAccepting] = useState(false);

  // suggestion.text 가 외부에서 갱신되면 편집 중이 아닐 때만 동기화.
  useEffect(() => {
    if (!editing) setDraft(suggestion.text);
  }, [suggestion.text, editing]);

  const handleSaveEdit = async () => {
    if (!draft.trim() || disabled) return;
    setSaving(true);
    try {
      await onEditSave(draft.trim());
      setEditing(false);
    } catch {
      // toast 는 호출자가 띄움 — 편집 모드는 그대로 유지해 재시도 가능.
    } finally {
      setSaving(false);
    }
  };

  const handleAcceptClick = async () => {
    setAccepting(true);
    try {
      await onAccept();
    } finally {
      setAccepting(false);
    }
  };

  return (
    <li className="flex flex-col gap-2 rounded-[--radius-m] border border-[--border] bg-[--surface] px-3 py-2.5">
      <div className="flex items-center gap-2">
        <Badge tone="brand">{suggestion.topic}</Badge>
        <span className="text-[10px] font-mono text-[--muted-foreground]">
          conf {suggestion.confidence.toFixed(2)} · evidence{" "}
          {suggestion.evidence_count}
        </span>
      </div>

      {editing ? (
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          spellCheck={false}
          rows={Math.max(2, Math.ceil(draft.length / 80))}
          className="w-full resize-y rounded-[--radius-m] border border-[--border] bg-[--card] p-2 text-sm leading-6 text-[--foreground] outline-none focus:border-[--primary]"
          aria-label={`제안 편집 — ${suggestion.id}`}
        />
      ) : (
        <p className="break-words text-sm leading-6 text-[--foreground]">
          {suggestion.text}
        </p>
      )}

      <div className="flex flex-wrap items-center justify-end gap-1.5">
        <Button
          variant="ghost"
          size="sm"
          onClick={onOpenSources}
          leftIcon={<Eye size={12} aria-hidden />}
        >
          근거 보기
        </Button>
        {editing ? (
          <>
            <Button
              variant="primary"
              size="sm"
              onClick={() => void handleSaveEdit()}
              disabled={
                saving ||
                disabled ||
                draft.trim() === "" ||
                draft.trim() === suggestion.text
              }
              leftIcon={
                saving ? (
                  <Loader2 size={12} aria-hidden className="animate-spin" />
                ) : (
                  <Save size={12} aria-hidden />
                )
              }
            >
              {saving ? "적용 중…" : "수정 적용"}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setDraft(suggestion.text);
                setEditing(false);
              }}
              disabled={saving}
              leftIcon={<X size={12} aria-hidden />}
            >
              취소
            </Button>
          </>
        ) : (
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setEditing(true)}
              disabled={disabled}
              leftIcon={<Pencil size={12} aria-hidden />}
            >
              편집
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={onRequestReject}
              disabled={disabled}
              leftIcon={<X size={12} aria-hidden />}
              className="text-[--color-error] hover:bg-[--color-error-bg]"
            >
              거절
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => void handleAcceptClick()}
              disabled={accepting || disabled}
              leftIcon={
                accepting ? (
                  <Loader2 size={12} aria-hidden className="animate-spin" />
                ) : (
                  <Check size={12} aria-hidden />
                )
              }
            >
              {accepting ? "적용 중…" : "적용"}
            </Button>
          </>
        )}
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Reject 확인 모달 — ConfirmGate(타이핑 일치) + 자유서술 reason.
// ---------------------------------------------------------------------------

interface RejectConfirmProps {
  target: Suggestion | null;
  onCancel: () => void;
  onConfirm: (reason: string) => void | Promise<void>;
}

function RejectConfirm({ target, onCancel, onConfirm }: RejectConfirmProps) {
  // ConfirmGate 의 children 슬롯이 reason textarea 를 담는다 — gate 재사용으로
  // 위험 의식 + 추가 메모 입력을 한 번에 수집한다.
  const [reason, setReason] = useState("");

  // target 변경 시 입력 리셋 — 이전 reason 가 다른 항목으로 흘러가지 않도록.
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
          거절하면 <span className="font-mono">{target?.topic}</span> 주제는 다음 드리밍 사이클부터
          블록리스트에 의해 자동으로 차단됩니다. 다시 허용하려면 블록리스트에서 직접 제거해야 해요.
          계속하려면 <span className="font-mono">REJECT</span>를 입력해 주세요.
        </>
      }
      confirmation="REJECT"
      confirmLabel="거절하고 블록"
      onConfirm={() => onConfirm(reason)}
    >
      <div className="flex flex-col gap-2">
        {target ? (
          <div className="rounded-[--radius-m] border border-[--border] bg-[--surface] px-3 py-2 text-xs">
            <div className="font-mono text-[10px] text-[--muted-foreground]">
              {target.topic} · {target.id}
            </div>
            <div className="mt-1 break-words text-[--foreground]">
              {target.text}
            </div>
          </div>
        ) : null}
        <label className="flex flex-col gap-1 text-xs text-[--muted-foreground]">
          <span>거절 사유 (선택, audit 로그에 남아요)</span>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={2}
            className={cn(
              "w-full resize-y rounded-[--radius-m] border border-[--border] bg-[--card] p-2 text-sm text-[--foreground] outline-none",
              "focus:border-[--primary]",
            )}
            placeholder="예: 일회성 농담이라 학습할 가치 없음"
          />
        </label>
      </div>
    </ConfirmGate>
  );
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

function formatTimestamp(iso: string): string {
  try {
    return new Date(iso).toLocaleString("ko-KR");
  } catch {
    return iso;
  }
}
