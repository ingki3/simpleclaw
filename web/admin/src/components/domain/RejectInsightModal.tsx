"use client";

/**
 * RejectInsightModal — BIZ-93 / admin.pen frame ``lVcRk``.
 *
 * 책임:
 *  - dreaming 인사이트 1건을 폐기하고 blocklist 에 등록할 때, **차단 기간**을
 *    운영자가 명시적으로 선택하게 한다.
 *  - 30 / 90 / 180 일 또는 영구 — 단일 선택, 90일 기본.
 *  - 제출 시 ``rejectSuggestion(id, { blocklist_period_days })`` 를 호출하고
 *    완료되면 onConfirmed 콜백으로 호출자에게 위임(토스트/리프레시는 호출자 몫).
 *
 * 비책임:
 *  - 토스트/큐 새로고침 — 호출자가 결과 + 에러를 받아 처리한다.
 *  - reason 자유서술 — BIZ-93 모달에는 포함되지 않는다(이전 ConfirmGate 흐름과 다름).
 *
 * 디자인 결정 (admin.pen DESIGN.md Patterns):
 *  - 헤더 아이콘은 ``ShieldX`` (lucide) — destructive 토큰을 적용해 위험도 가시화.
 *  - 푸터 primary 액션은 ``destructive`` 변형 — \"폐기 + Blocklist 등록\" 라벨.
 *  - 기간 선택 row: 4개 토글 버튼. 선택된 버튼은 primary, 그 외 outline.
 *  - 제출 중에는 모달 dismissible=false (오류로 닫혀 상태가 어긋나지 않게).
 *  - 90일 기본은 \"한 번 거절했지만 분기 정도 지나서 다시 보고 싶을 수도\" 라는
 *    운영 시나리오에서 도출한 값.
 */

import { useEffect, useState, type ReactNode } from "react";
import { Loader2, ShieldX } from "lucide-react";
import { Button } from "@/components/atoms/Button";
import { Modal } from "@/components/primitives/Modal";
import { cn } from "@/lib/cn";
import type { RejectBlocklistPeriodDays } from "@/lib/api/suggestions";

/** UI 상에서 \"영구\" 선택지를 표현하는 sentinel — 직렬화 시 ``null`` 로 전송. */
const PERMANENT = "permanent" as const;

type PeriodChoice = 30 | 90 | 180 | typeof PERMANENT;

interface PeriodOption {
  value: PeriodChoice;
  label: string;
  ariaLabel: string;
}

const PERIOD_OPTIONS: PeriodOption[] = [
  { value: 30, label: "30일", ariaLabel: "30일 차단" },
  { value: 90, label: "90일", ariaLabel: "90일 차단" },
  { value: 180, label: "180일", ariaLabel: "180일 차단" },
  { value: PERMANENT, label: "영구", ariaLabel: "영구 차단" },
];

const DEFAULT_PERIOD: PeriodChoice = 90;

export interface RejectInsightModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 모달 헤더에 readonly 로 표시할 topic key — dreaming 이 도출한 정규형. */
  topic: string;
  /** topic 외에 인사이트 본문 미리보기를 함께 노출하고 싶을 때. 미지정 시 생략. */
  bodyPreview?: ReactNode;
  /**
   * 운영자가 \"폐기 + Blocklist 등록\" 을 누른 직후 실행되는 핸들러.
   * 비동기 실패는 호출자가 토스트로 노출 — 본 모달은 에러 시 모달을 유지해
   * 재시도가 가능하도록 한다.
   */
  onConfirm: (period: RejectBlocklistPeriodDays) => Promise<void> | void;
}

export function RejectInsightModal({
  open,
  onOpenChange,
  topic,
  bodyPreview,
  onConfirm,
}: RejectInsightModalProps) {
  // 매번 모달이 다시 열릴 때 90일 기본값으로 초기화 — 이전 선택이 누설되지 않도록.
  const [period, setPeriod] = useState<PeriodChoice>(DEFAULT_PERIOD);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setPeriod(DEFAULT_PERIOD);
      setError(null);
      setPending(false);
    }
  }, [open]);

  async function handleConfirm() {
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      const value: RejectBlocklistPeriodDays =
        period === PERMANENT ? null : period;
      await onConfirm(value);
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(false);
    }
  }

  return (
    <Modal
      open={open}
      onOpenChange={(o) => {
        if (pending) return; // 진행 중에는 ESC/바깥 클릭으로 닫지 않는다.
        onOpenChange(o);
      }}
      // 헤더 아이콘 + 제목을 직접 합성 — Modal.title 은 ReactNode 를 그대로 받는다.
      title={
        <span className="flex items-center gap-2">
          <span
            aria-hidden
            className="grid h-7 w-7 place-items-center rounded-(--radius-m) bg-(--destructive)/15 text-(--destructive)"
          >
            <ShieldX size={16} />
          </span>
          이 인사이트를 폐기하고 blocklist에 등록할까요?
        </span>
      }
      description="선택한 기간 동안 같은 토픽이 다시 추출되지 않도록 차단해요. 영구를 선택하면 운영자가 직접 해제해야 다시 학습됩니다."
      alert
      dismissible={!pending}
      size="sm"
      footer={
        <>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onOpenChange(false)}
            disabled={pending}
          >
            취소
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={() => void handleConfirm()}
            disabled={pending}
            leftIcon={
              pending ? (
                <Loader2 size={12} aria-hidden className="animate-spin" />
              ) : null
            }
          >
            {pending ? "등록 중…" : "폐기 + Blocklist 등록"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {/* topic readonly card — dreaming 이 도출한 키, 사용자 수정 불가. */}
        <section
          aria-label="대상 토픽"
          className="rounded-(--radius-m) border border-(--border) bg-(--surface) px-3 py-2.5"
        >
          <div className="text-[10px] font-mono uppercase tracking-wide text-(--muted-foreground)">
            대상 토픽
          </div>
          <div className="mt-1 break-words font-mono text-sm text-(--foreground-strong)">
            {topic || "—"}
          </div>
          {bodyPreview ? (
            <div className="mt-2 break-words text-xs text-(--muted-foreground)">
              {bodyPreview}
            </div>
          ) : null}
        </section>

        {/* 차단 기간 선택 row — 단일 선택, 90 default, 필수 선택 (모두 prefilled 이므로
            disabled 분기 없음). */}
        <fieldset className="flex flex-col gap-2">
          <legend className="text-xs font-medium text-(--foreground)">
            차단 기간
          </legend>
          <div
            role="radiogroup"
            aria-label="blocklist 차단 기간"
            className="grid grid-cols-4 gap-1.5"
          >
            {PERIOD_OPTIONS.map((opt) => {
              const selected = period === opt.value;
              return (
                <button
                  key={String(opt.value)}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  aria-label={opt.ariaLabel}
                  onClick={() => setPeriod(opt.value)}
                  disabled={pending}
                  className={cn(
                    "rounded-(--radius-m) border px-3 py-2 text-sm font-medium transition-colors",
                    "disabled:cursor-not-allowed disabled:opacity-50",
                    selected
                      ? "border-(--destructive) bg-(--destructive)/10 text-(--destructive)"
                      : "border-(--border) bg-(--card) text-(--foreground) hover:bg-(--surface)",
                  )}
                >
                  {opt.label}
                </button>
              );
            })}
          </div>
          <p className="text-[11px] text-(--muted-foreground)">
            기본값 90일 — 분기 단위 재검토. 영구는 운영자가 직접 해제해야 풀려요.
          </p>
        </fieldset>

        {error ? (
          <div
            role="alert"
            className="rounded-(--radius-m) border border-(--color-error) bg-(--color-error-bg) px-3 py-2 text-xs text-(--color-error)"
          >
            등록에 실패했어요: {error}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
