"use client";

/**
 * ConfirmRestartDialog — admin.pen `RM5Ar` (Confirm Restart Dialog) 박제.
 *
 * RestartCard 의 "데몬 재시작…" 트리거 시 진입. DESIGN.md §4.9 Restart Required 패턴:
 * 프로세스(데몬) 재시작과 서비스(데몬 + 채널) 재시작 두 옵션을 분리 노출하고,
 * 운영자가 선택한 후 ConfirmGate 로 키워드 + 카운트다운을 통과해야 실행된다.
 *
 * 본 단계는 mutation 자체를 mock — 실제 데몬 재시작 호출은 데몬 통합 단계에서.
 */

import { useEffect, useState } from "react";
import { Button } from "@/design/atoms/Button";
import { ConfirmGate } from "@/design/molecules/ConfirmGate";
import { cn } from "@/lib/cn";
import { Modal } from "./Modal";
import type { RestartInfo } from "../_data";

export type RestartScope = "process" | "service";

interface ConfirmRestartDialogProps {
  open: boolean;
  info: RestartInfo;
  onClose: () => void;
  /** 카운트다운 + 키워드 게이트 통과 시 호출. scope 는 프로세스/서비스 중 하나. */
  onConfirm: (scope: RestartScope) => void;
}

const SCOPES: readonly { value: RestartScope; title: string; detail: string }[] = [
  {
    value: "process",
    title: "프로세스 재시작",
    detail: "데몬 프로세스만 재시작 (~10s). 채널/큐는 유지됩니다.",
  },
  {
    value: "service",
    title: "서비스 전체 재시작",
    detail: "데몬 + 채널/큐 모두 재시작 (~30s). 진행 중 작업이 중단됩니다.",
  },
];

export function ConfirmRestartDialog({
  open,
  info,
  onClose,
  onConfirm,
}: ConfirmRestartDialogProps) {
  const [scope, setScope] = useState<RestartScope>("process");

  // 모달이 열릴 때마다 scope 를 기본값으로 리셋 — 잘못된 직전 선택의 잔존을 방지.
  useEffect(() => {
    if (open) setScope("process");
  }, [open]);

  return (
    <Modal
      open={open}
      onClose={onClose}
      data-testid="confirm-restart-dialog"
      width="md"
      title={
        <div className="flex items-center gap-2">
          <span aria-hidden className="text-(--color-warning)">
            ⚠
          </span>
          <h2 className="text-lg font-semibold text-(--foreground-strong)">
            데몬을 재시작하시겠습니까?
          </h2>
        </div>
      }
      footer={
        <Button variant="ghost" size="sm" onClick={onClose}>
          닫기
        </Button>
      }
    >
      <p className="text-sm text-(--muted-foreground)">{info.impactSummary}</p>

      <fieldset
        className="flex flex-col gap-2"
        data-testid="confirm-restart-scopes"
      >
        <legend className="sr-only">재시작 범위</legend>
        {SCOPES.map((opt) => {
          const active = scope === opt.value;
          return (
            <button
              key={opt.value}
              type="button"
              role="radio"
              aria-checked={active}
              data-testid={`confirm-restart-scope-${opt.value}`}
              data-active={active || undefined}
              onClick={() => setScope(opt.value)}
              className={cn(
                "flex flex-col items-start gap-1 rounded-(--radius-m) border px-3 py-2.5 text-left transition-colors",
                active
                  ? "border-(--primary) bg-(--primary-tint)"
                  : "border-(--border) bg-(--surface) hover:border-(--border-strong)",
              )}
            >
              <span className="text-sm font-medium text-(--foreground-strong)">
                {opt.title}
              </span>
              <span className="text-xs text-(--muted-foreground)">
                {opt.detail}
              </span>
            </button>
          );
        })}
      </fieldset>

      <ConfirmGate
        keyword="restart"
        confirmLabel="재시작"
        cancelLabel="취소"
        onConfirm={() => {
          onConfirm(scope);
          onClose();
        }}
        onCancel={onClose}
        description={
          <span data-testid="confirm-restart-description">
            마지막 재시작 — {info.lastRestart} ({info.lastRestartRelative}).
            실행 후 약 10 초간 서비스가 중지됩니다.
          </span>
        }
      />
    </Modal>
  );
}
