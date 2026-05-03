"use client";

/**
 * SuggestionQueuePanel (BIZ-79) — Dreaming Dry-run + Admin Review Loop.
 *
 * 큐 의미:
 * - dreaming 결과는 곧바로 USER.md 에 반영되지 않고 본 패널의 pending 리스트에 뜬다.
 * - 운영자가 행마다 Accept / Edit / Reject 중 하나를 선택한다.
 *   - Accept: insights.jsonl sidecar 에 즉시 등재 → 다음 dreaming 사이클이 USER.md
 *     반영을 마친다.
 *   - Edit: 본문 텍스트만 갱신하고 status 는 pending 유지 — 운영자가 표현을 다듬은
 *     뒤 Accept/Reject 를 결정하는 2-stage 흐름을 지원.
 *   - Reject: blocklist 에 추가 → 다음 dreaming 사이클이 같은 topic 을 다시 추출하지
 *     않게 차단한다 (DoD §B 의 핵심 가드).
 *
 * 디자인 결정:
 * - 0건 상태도 카드는 항상 보여 준다 — 운영자가 큐 자체의 존재를 학습할 수 있도록.
 * - 행 단위 mutation 은 낙관적 업데이트 없이 round-trip 후 list 를 새로 가져온다.
 *   Source linkage(BIZ-77) 와 충돌하는 즉시 검증을 피하려는 보수적 선택.
 */

import { useCallback, useEffect, useState } from "react";
import { Check, Edit2, Inbox, X } from "lucide-react";
import { Badge } from "@/components/atoms/Badge";
import { Button } from "@/components/atoms/Button";
import { Input } from "@/components/atoms/Input";
import {
  type SuggestionItem,
  acceptSuggestion,
  editSuggestion,
  listSuggestions,
  rejectSuggestion,
} from "@/lib/api/suggestions";

interface SuggestionQueuePanelProps {
  /** 행 mutation 후 호출 — 부모(Memory 페이지)가 인덱스를 새로고침할 때 사용. */
  onMutated?: () => void;
}

type RowState =
  | { mode: "view" }
  | { mode: "edit"; text: string };

export function SuggestionQueuePanel({ onMutated }: SuggestionQueuePanelProps) {
  const [items, setItems] = useState<SuggestionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // 행별 모드 (view/edit) — topic 키. edit 텍스트도 같이 보관.
  const [rowState, setRowState] = useState<Record<string, RowState>>({});
  // 진행 중 액션 — 같은 행에서 더블 클릭 방지.
  const [pendingTopic, setPendingTopic] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listSuggestions();
      setItems(res.items);
    } catch (e) {
      // 503 (큐 미주입) 은 운영 환경에서 정상 — 에러 텍스트로 안내.
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const setEdit = (topic: string, text: string) =>
    setRowState((prev) => ({ ...prev, [topic]: { mode: "edit", text } }));
  const setView = (topic: string) =>
    setRowState((prev) => ({ ...prev, [topic]: { mode: "view" } }));

  const handleAccept = async (topic: string) => {
    setPendingTopic(topic);
    try {
      await acceptSuggestion(topic);
      await refresh();
      onMutated?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPendingTopic(null);
    }
  };

  const handleReject = async (topic: string) => {
    setPendingTopic(topic);
    try {
      await rejectSuggestion(topic);
      await refresh();
      onMutated?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPendingTopic(null);
    }
  };

  const handleEditSave = async (topic: string, text: string) => {
    if (!text.trim()) return;
    setPendingTopic(topic);
    try {
      await editSuggestion(topic, text.trim());
      setView(topic);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPendingTopic(null);
    }
  };

  return (
    <section
      aria-labelledby="suggestion-queue-title"
      className="flex flex-col gap-3 rounded-[--radius-l] border border-[--border] bg-[--card] p-5"
    >
      <header className="flex items-center justify-between">
        <div>
          <h2
            id="suggestion-queue-title"
            className="flex items-center gap-2 text-sm font-semibold text-[--foreground-strong]"
          >
            <Inbox size={14} aria-hidden /> 인사이트 검수 큐
          </h2>
          <p className="mt-1 text-xs text-[--muted-foreground]">
            드리밍이 새로 발견한 인사이트입니다. Accept(승격) · Edit(수정) ·
            Reject(차단) 으로 검수해 주세요.
          </p>
        </div>
        <Badge tone="neutral">{items.length}</Badge>
      </header>

      {error ? (
        <div className="rounded-[--radius-m] border border-[--color-error] bg-[--color-error-bg] px-3 py-2 text-xs text-[--color-error]">
          큐를 불러오지 못했어요: {error}
        </div>
      ) : loading ? (
        <div className="text-xs text-[--muted-foreground]">불러오는 중…</div>
      ) : items.length === 0 ? (
        <div className="rounded-[--radius-m] border border-dashed border-[--border] bg-[--surface] px-4 py-6 text-center text-xs text-[--muted-foreground]">
          검수할 새 인사이트가 없어요.
        </div>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((item) => {
            const state = rowState[item.topic] ?? { mode: "view" };
            const isPending = pendingTopic === item.topic;
            return (
              <li
                key={item.topic}
                className="rounded-[--radius-m] border border-[--border] bg-[--surface] p-3"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2 text-xs text-[--muted-foreground]">
                      <span className="font-mono text-[--foreground-strong]">
                        {item.topic}
                      </span>
                      <Badge tone="info">관측 {item.evidence_count}회</Badge>
                      <Badge tone="neutral">
                        confidence {item.confidence.toFixed(2)}
                      </Badge>
                    </div>
                    {state.mode === "edit" ? (
                      <Input
                        value={state.text}
                        onChange={(e) => setEdit(item.topic, e.target.value)}
                        aria-label="인사이트 텍스트 편집"
                        className="mt-2 w-full"
                      />
                    ) : (
                      <p className="mt-2 break-words text-sm text-[--foreground]">
                        {item.text}
                      </p>
                    )}
                  </div>
                </div>
                <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
                  {state.mode === "edit" ? (
                    <>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setView(item.topic)}
                        disabled={isPending}
                      >
                        취소
                      </Button>
                      <Button
                        variant="primary"
                        size="sm"
                        onClick={() => void handleEditSave(item.topic, state.text)}
                        disabled={isPending || !state.text.trim()}
                      >
                        저장
                      </Button>
                    </>
                  ) : (
                    <>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setEdit(item.topic, item.text)}
                        leftIcon={<Edit2 size={12} aria-hidden />}
                        disabled={isPending}
                      >
                        Edit
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        onClick={() => void handleReject(item.topic)}
                        leftIcon={<X size={12} aria-hidden />}
                        disabled={isPending}
                      >
                        Reject
                      </Button>
                      <Button
                        variant="primary"
                        size="sm"
                        onClick={() => void handleAccept(item.topic)}
                        leftIcon={<Check size={12} aria-hidden />}
                        disabled={isPending}
                      >
                        Accept
                      </Button>
                    </>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
