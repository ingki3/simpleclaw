"use client";

/**
 * InsightCard — BIZ-92 / BIZ-90 §검토(Review) 카드.
 *
 * 책임:
 *  - 한 건의 인사이트(또는 검토 대기 suggestion) 를 카드로 렌더.
 *  - confidence 를 텍스트 + 색(green/amber/red) 으로 동시에 노출 — 색맹 접근성.
 *  - evidence ×N · last_seen · channel 메타 라인.
 *  - 액션: Defer / Edit / Reject / Accept (variant=review)
 *           low-conf+cron-derived → 단일 ``Reject + Blocklist`` 버튼.
 *  - Active/Archive 탭에서는 액션 없이 메타만 노출 (variant=read).
 *
 * 비책임:
 *  - 데이터 fetch / mutation 호출 — 부모 페이지가 callback 으로 처리.
 *  - 인라인 편집 textarea — 이전 SuggestionQueuePanel 의 SuggestionRow 가
 *    이미 그 책임을 가진다. 이 카드는 "편집 진입" 버튼만 노출하고 실제 편집은
 *    상위에서 모드 전환으로 다룬다 (BIZ-93/94 sub-issue 에서 인라인 편집 분리).
 */

import type { ReactNode } from "react";
import { Check, Eye, Pause, Pencil, Sparkles, X } from "lucide-react";
import { Badge, type BadgeTone } from "@/components/atoms/Badge";
import { Button } from "@/components/atoms/Button";
import { cn } from "@/lib/cn";

// ---------------------------------------------------------------------------
// confidence -> 시각 분류 — BIZ-90 합격 기준 1.
// ---------------------------------------------------------------------------

export type ConfidenceLevel = "high" | "medium" | "low";

/** ≥0.7 high(green) / 0.4–0.7 medium(amber) / <0.4 low(red). */
export function classifyConfidence(value: number): ConfidenceLevel {
  if (value >= 0.7) return "high";
  if (value >= 0.4) return "medium";
  return "low";
}

const CONFIDENCE_TONE: Record<ConfidenceLevel, BadgeTone> = {
  high: "success",
  medium: "warning",
  low: "danger",
};

const CONFIDENCE_LABEL: Record<ConfidenceLevel, string> = {
  high: "높음",
  medium: "보통",
  low: "낮음",
};

// ---------------------------------------------------------------------------
// 카드 props — Suggestion / InsightItem / BlocklistEntry 의 공통 표현.
// ---------------------------------------------------------------------------

export interface InsightCardData {
  /** 사용자 표시용 토픽 (일반 + 블록리스트 공통). */
  topic: string;
  /** 본문 — 블록리스트는 reason 을 쓴다. */
  text: string;
  /** 0..1. blocklist/archive 등 confidence 가 의미 없으면 null. */
  confidence: number | null;
  /** 누적 evidence 수. blocklist 에서는 null. */
  evidenceCount: number | null;
  /** ISO 8601 — last_seen / archived_at / blocked_at 중 어떤 시각이든 가능. */
  timestamp: string | null;
  /** 시각 옆에 붙는 라벨 — "마지막 관찰" / "보관" / "차단" 등. */
  timestampLabel?: string;
  /** 정확한 채널 — 백엔드가 미제공이면 null. */
  channel?: string | null;
  /** "evidence ×N" 등 추가 메타 — 메타 라인에 마지막에 붙는다. */
  extraMeta?: ReactNode;
}

export interface InsightCardActions {
  /** 클릭 시 부모가 sources drawer/modal 을 연다. */
  onOpenSources?: () => void;
  onAccept?: () => void;
  onEdit?: () => void;
  onReject?: () => void;
  onDefer?: () => void;
  /** 액션 진행 중 등 외부에서 모든 버튼을 잠글 때. */
  disabled?: boolean;
  /**
   * cron/recipe 등 자동 노이즈 → 적용보다는 차단이 적절한 경우.
   * true 면 Defer/Edit/Accept 가 사라지고 ``Reject + Blocklist`` 단일 버튼으로 압축.
   */
  rejectOnly?: boolean;
}

export interface InsightCardProps extends InsightCardActions {
  data: InsightCardData;
  /**
   * "review" — 액션 풀세트 (Defer/Edit/Reject/Accept) + 강조 보더.
   * "read"   — 메타만, 액션 영역 자체 비노출 (Active/Archive/Blocklist).
   */
  variant?: "review" | "read";
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function InsightCard({
  data,
  variant = "review",
  onOpenSources,
  onAccept,
  onEdit,
  onReject,
  onDefer,
  disabled,
  rejectOnly,
}: InsightCardProps) {
  const level =
    typeof data.confidence === "number"
      ? classifyConfidence(data.confidence)
      : null;

  // confidence 가 있을 때는 "{label} {value}" 라벨을 한 단어로 묶어 시각/텍스트
  // 동시 표현(접근성). 0.84 → "높음 0.84".
  const confidenceText =
    level !== null && data.confidence !== null
      ? `${CONFIDENCE_LABEL[level]} ${data.confidence.toFixed(2)}`
      : null;

  return (
    <article
      className={cn(
        "flex flex-col gap-2 rounded-(--radius-m) border bg-(--surface) px-3 py-3",
        variant === "review"
          ? "border-(--border)"
          : "border-(--border) bg-(--card)",
      )}
    >
      {/* 헤더: 토픽 + confidence + evidence + 메타 */}
      <header className="flex flex-wrap items-center gap-2">
        <Badge tone="brand">{data.topic}</Badge>
        {confidenceText && level ? (
          <Badge tone={CONFIDENCE_TONE[level]} aria-label={`신뢰도 ${confidenceText}`}>
            {confidenceText}
          </Badge>
        ) : null}
        {typeof data.evidenceCount === "number" ? (
          <span className="text-[10px] font-mono text-(--muted-foreground)">
            evidence ×{data.evidenceCount}
          </span>
        ) : null}
        {data.channel ? (
          <span className="text-[10px] font-mono text-(--muted-foreground)">
            · {data.channel}
          </span>
        ) : null}
        {data.timestamp ? (
          <span className="text-[10px] font-mono text-(--muted-foreground)">
            · {data.timestampLabel ?? "마지막 관찰"}{" "}
            {formatTimestamp(data.timestamp)}
          </span>
        ) : null}
        {data.extraMeta ? (
          <span className="text-[10px] font-mono text-(--muted-foreground)">
            {data.extraMeta}
          </span>
        ) : null}
      </header>

      {/* 본문 */}
      <p className="break-words text-sm leading-6 text-(--foreground)">
        {data.text}
      </p>

      {/* 소스 진입점 */}
      {onOpenSources ? (
        <div>
          <button
            type="button"
            onClick={onOpenSources}
            disabled={disabled}
            className="inline-flex items-center gap-1 text-xs text-(--primary) hover:underline disabled:opacity-50"
          >
            <Eye size={12} aria-hidden /> 원문 보기 →
          </button>
        </div>
      ) : null}

      {/* 액션 영역 — review variant 에서만 */}
      {variant === "review" ? (
        <div className="flex flex-wrap items-center justify-end gap-1.5">
          {rejectOnly ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={onReject}
              disabled={disabled || !onReject}
              leftIcon={<X size={12} aria-hidden />}
              className="text-(--color-error) hover:bg-(--color-error-bg)"
            >
              Reject + Blocklist
            </Button>
          ) : (
            <>
              {onDefer ? (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={onDefer}
                  disabled={disabled}
                  leftIcon={<Pause size={12} aria-hidden />}
                >
                  보류
                </Button>
              ) : null}
              {onEdit ? (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={onEdit}
                  disabled={disabled}
                  leftIcon={<Pencil size={12} aria-hidden />}
                >
                  편집
                </Button>
              ) : null}
              {onReject ? (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={onReject}
                  disabled={disabled}
                  leftIcon={<X size={12} aria-hidden />}
                  className="text-(--color-error) hover:bg-(--color-error-bg)"
                >
                  거절
                </Button>
              ) : null}
              {onAccept ? (
                <Button
                  variant="primary"
                  size="sm"
                  onClick={onAccept}
                  disabled={disabled}
                  leftIcon={<Check size={12} aria-hidden />}
                >
                  적용
                </Button>
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </article>
  );
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function formatTimestamp(iso: string): string {
  try {
    return new Date(iso).toLocaleString("ko-KR", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// 빈/로딩 placeholder — 페이지에서 재사용.
// ---------------------------------------------------------------------------

export function InsightCardEmpty({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-(--radius-m) border border-dashed border-(--border) bg-(--surface) px-3 py-8 text-center text-xs text-(--muted-foreground)">
      <Sparkles size={14} aria-hidden className="mx-auto mb-1 opacity-60" />
      {children}
    </div>
  );
}
