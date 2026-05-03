"use client";

/**
 * RestartActions — System 화면의 데몬/프로세스 재시작 액션 카드.
 *
 * 두 가지 액션:
 *  - "Daemon 재시작" (⏻)        — pending 변경을 yaml에 머지하고 데몬을 살짝 재기동.
 *  - "Process 재시작" (⏻⏻)      — 호스트 프로세스 자체를 죽이고 다시 띄움 (호스트 재기동에
 *                                  준하는 강한 액션).
 *
 * 각 액션은 다음 두 단계를 통과해야 호출된다:
 *   1) ConfirmGate — 운영자가 정확한 토큰(`RESTART` / `RESTART-PROCESS`)을 입력해야 진행.
 *   2) RestartStepper — DESIGN.md §4.9의 5단계 stepper(pending→dry-run→confirm→applying→done)
 *      를 표시하면서 백엔드 `/admin/v1/system/restart`를 호출, 결과/에러를 5단계에 매핑.
 *
 * 백엔드는 *재시작 모드를 구분하지 않는다*. 본 화면은 두 모드를 다른 이유 문자열
 * (`reason`)로 보내고, 실제 프로세스 종료까지 가는 것은 호스트가 주입한
 * `restart_callback`이 책임진다 — 본 컴포넌트는 호출만 수행하고, "결과" 단계에서
 * 응답의 `applied_pending`을 노출한다.
 *
 * 진행 흐름은 백엔드 호출이 동기적으로 끝나면 즉시 `done`으로 진입한다. 호출 자체가
 * 실패해도 stepper 모달은 열린 채 `failed=true`로 사용자에게 다음 행동을 안내한다.
 */

import { useCallback, useState } from "react";
import { Power } from "lucide-react";
import { Badge } from "@/components/atoms/Badge";
import { Button } from "@/components/atoms/Button";
import { SettingCard } from "@/components/molecules/SettingCard";
import { ConfirmGate } from "@/components/primitives/ConfirmGate";
import {
  RestartStepper,
  type RestartStep,
} from "@/components/primitives/RestartStepper";
import { useToast } from "@/lib/toast";
import {
  fetchAdmin,
  AdminApiError,
  type SystemRestartResponse,
} from "@/lib/api";

export type RestartMode = "daemon" | "process";

interface ModeMeta {
  /** 카드의 1차 라벨 — admin.pen 표기를 따른다. */
  ctaLabel: string;
  /** 카드 옆의 짧은 설명. */
  blurb: string;
  /** ConfirmGate에서 운영자가 입력해야 하는 토큰. */
  confirmation: string;
  /** stepper 결과 단계 마무리 라벨. */
  doneLabel: string;
  /** 백엔드에 전송하는 reason. 감사 로그에 그대로 들어간다. */
  reason: string;
  /** 액션 버튼에 표시할 강도 표기 — "⏻" 또는 "⏻⏻". */
  power: string;
}

const MODE_META: Record<RestartMode, ModeMeta> = {
  daemon: {
    ctaLabel: "Daemon 재시작",
    blurb:
      "펜딩된 변경을 적용하고 데몬을 재기동합니다. config.yaml은 자동 머지됩니다.",
    confirmation: "RESTART",
    doneLabel: "확인",
    reason: "admin-ui:daemon",
    power: "⏻",
  },
  process: {
    ctaLabel: "Process 재시작",
    blurb:
      "호스트 프로세스 자체를 종료 후 다시 띄웁니다. 모든 연결이 끊어집니다.",
    confirmation: "RESTART-PROCESS",
    doneLabel: "닫기",
    reason: "admin-ui:process",
    power: "⏻⏻",
  },
};

interface FlowState {
  mode: RestartMode;
  step: RestartStep;
  failed: boolean;
  result?: SystemRestartResponse;
  errorMessage?: string;
}

export interface RestartActionsProps {
  /** 헤더 카드의 펜딩 변경 카운트(헬스 응답 기반). */
  pendingChanges?: boolean;
  /** 재시작 후 부모가 헬스/info를 다시 가져올 수 있도록 알림. */
  onRestartCompleted?: () => void;
}

export function RestartActions({
  pendingChanges,
  onRestartCompleted,
}: RestartActionsProps) {
  const toast = useToast();

  // 단계별 모달은 한 번에 하나만 열리지만, "어떤 모달이 어떤 모드인지"는 명시 상태로 둔다.
  const [confirmOpen, setConfirmOpen] = useState<RestartMode | null>(null);
  const [flow, setFlow] = useState<FlowState | null>(null);

  const launch = useCallback(
    async (mode: RestartMode) => {
      const meta = MODE_META[mode];
      // 단계 진입 — pending → applying → done|failed.
      setFlow({ mode, step: "applying", failed: false });
      try {
        const result = await fetchAdmin<SystemRestartResponse>(
          "/system/restart",
          {
            method: "POST",
            json: { reason: meta.reason },
          },
        );
        setFlow({ mode, step: "done", failed: false, result });
        toast.push({
          tone: "success",
          title: `${meta.ctaLabel} 요청을 전송했습니다.`,
          description:
            result.applied_pending > 0
              ? `펜딩 변경 ${result.applied_pending}건이 적용되었습니다.`
              : "펜딩 변경 없이 재시작 명령만 전달되었습니다.",
        });
        onRestartCompleted?.();
      } catch (err) {
        const message =
          err instanceof AdminApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : String(err);
        setFlow({ mode, step: "done", failed: true, errorMessage: message });
        toast.push({
          tone: "error",
          title: `${meta.ctaLabel} 요청 실패`,
          description: message,
        });
      }
    },
    [onRestartCompleted, toast],
  );

  return (
    <>
      <SettingCard
        title="재시작 액션"
        description="변경된 설정을 즉시 반영하거나 호스트 프로세스를 다시 띄웁니다. 두 단계 확인을 거칩니다."
        headerRight={
          pendingChanges ? <Badge tone="warning">⏻ 펜딩 변경 있음</Badge> : null
        }
      >
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {(["daemon", "process"] as const).map((mode) => {
            const meta = MODE_META[mode];
            return (
              <div
                key={mode}
                className="flex flex-col gap-3 rounded-[--radius-m] border border-[--border] bg-[--surface] p-4"
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold text-[--foreground-strong]">
                      {meta.ctaLabel}
                    </p>
                    <p className="text-xs text-[--muted-foreground]">
                      확인 토큰:{" "}
                      <code className="font-mono text-[--foreground]">
                        {meta.confirmation}
                      </code>
                    </p>
                  </div>
                  <span
                    aria-hidden
                    className="text-base text-[--color-warning]"
                    title={meta.power}
                  >
                    {meta.power}
                  </span>
                </div>
                <p className="text-sm text-[--muted-foreground]">{meta.blurb}</p>
                <Button
                  variant={mode === "process" ? "destructive" : "primary"}
                  size="sm"
                  leftIcon={<Power size={14} aria-hidden />}
                  onClick={() => setConfirmOpen(mode)}
                >
                  {meta.ctaLabel}
                </Button>
              </div>
            );
          })}
        </div>
      </SettingCard>

      {/* ConfirmGate — 운영자 의사 1차 확인. confirm 통과 시 stepper로 승급한다. */}
      {(["daemon", "process"] as const).map((mode) => {
        const meta = MODE_META[mode];
        return (
          <ConfirmGate
            key={mode}
            open={confirmOpen === mode}
            onOpenChange={(open) => setConfirmOpen(open ? mode : null)}
            title={`${meta.ctaLabel}을 진행할까요?`}
            description={meta.blurb}
            confirmation={meta.confirmation}
            confirmLabel="재시작 절차 시작"
            tone={mode === "process" ? "destructive" : "warning"}
            onConfirm={async () => {
              setConfirmOpen(null);
              await launch(mode);
            }}
          />
        );
      })}

      {/* Stepper — applying/done 단계만 표시한다. ConfirmGate가 1·3단계를 흡수했고,
          dry-run은 백엔드에 mode-specific 스키마가 없어 stepper 시각으로만 통과한다. */}
      {flow ? (
        <RestartStepper
          open={flow !== null}
          onOpenChange={(open) => {
            if (!open) setFlow(null);
          }}
          step={flow.step}
          failed={flow.failed}
          doneLabel={MODE_META[flow.mode].doneLabel}
        >
          <FlowBody flow={flow} />
        </RestartStepper>
      ) : null}
    </>
  );
}

function FlowBody({ flow }: { flow: FlowState }) {
  const meta = MODE_META[flow.mode];
  if (flow.step === "applying") {
    return (
      <p className="text-sm text-[--muted-foreground]">
        {meta.ctaLabel} 요청을 데몬에 전송 중입니다…
      </p>
    );
  }
  if (flow.step === "done" && flow.failed) {
    return (
      <p className="text-sm text-[--color-error]">
        요청이 실패했습니다: {flow.errorMessage ?? "알 수 없는 오류"}
      </p>
    );
  }
  if (flow.step === "done" && flow.result) {
    return (
      <div className="flex flex-col gap-1 text-sm text-[--foreground]">
        <p>
          ✓ {meta.ctaLabel} 요청을 수락했습니다. (audit:{" "}
          <code className="font-mono text-[--muted-foreground]">
            {flow.result.audit_id.slice(0, 8)}
          </code>
          )
        </p>
        <p className="text-xs text-[--muted-foreground]">
          적용된 펜딩 변경: {flow.result.applied_pending}건
        </p>
      </div>
    );
  }
  return null;
}
