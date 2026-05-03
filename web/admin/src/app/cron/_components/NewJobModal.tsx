"use client";

/**
 * 새 잡 생성 모달.
 *
 * 폼: 이름 / 표현식 (검증·미리보기) / 액션 종류·참조 / 활성 토글.
 * "Dry-run" 버튼은 표현식 검증과 동일한 ``previewCronExpression`` 응답을
 * 모달 하단에 노출 — 사용자가 저장 전에 한 번 더 확인할 수 있게 한다.
 *
 * 검증 게이트:
 * - 이름: 비어 있지 않고 ``/^[a-z0-9_-]{1,64}$/`` (영문 소문자/숫자/언더스코어/하이픈)
 * - 표현식: ``validateCronExpression`` 통과
 * - 액션 참조: 비어 있지 않음
 *
 * 모두 통과해야 ``저장``이 활성화된다.
 */

import { useState } from "react";
import { Input } from "@/components/atoms/Input";
import { Switch } from "@/components/atoms/Switch";
import { Button } from "@/components/atoms/Button";
import { Modal } from "../_primitives/Modal";
import { ExpressionInput } from "./ExpressionInput";
import {
  createCronJob,
  previewCronExpression,
} from "@/lib/cron/client";
import type {
  CronActionType,
  CronJob,
  CronJobInput,
} from "@/lib/cron/types";

export interface NewJobModalProps {
  open: boolean;
  onClose: () => void;
  onCreated: (job: CronJob) => void;
}

const NAME_PATTERN = /^[a-z0-9_-]{1,64}$/;

export function NewJobModal({ open, onClose, onCreated }: NewJobModalProps) {
  const [name, setName] = useState("");
  const [expr, setExpr] = useState("0 9 * * *");
  const [actionType, setActionType] = useState<CronActionType>("prompt");
  const [actionRef, setActionRef] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [exprValid, setExprValid] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dryRunResult, setDryRunResult] = useState<string | null>(null);

  // 모달이 다시 열릴 때 잔재가 남지 않도록 close 시 부모가 unmount 한다고 가정.

  const nameValid = NAME_PATTERN.test(name);
  const refValid = actionRef.trim().length > 0;
  const ready = nameValid && exprValid && refValid && !submitting;

  const handleDryRun = async () => {
    setError(null);
    const r = await previewCronExpression(expr, 3);
    if (!r.valid) {
      setDryRunResult(`표현식 오류 — ${r.error}`);
      return;
    }
    setDryRunResult(
      `OK — ${r.description}. 다음 3회: ${r.nextRuns
        .map((s) => new Date(s).toLocaleString("ko-KR"))
        .join(", ")}`,
    );
  };

  const handleSubmit = async () => {
    if (!ready) return;
    setSubmitting(true);
    setError(null);
    try {
      const input: CronJobInput = {
        name,
        cronExpression: expr,
        actionType,
        actionReference: actionRef,
        enabled,
      };
      const created = await createCronJob(input);
      onCreated(created);
      // 입력 초기화 — 같은 세션에서 연속 생성도 깔끔히 받도록.
      setName("");
      setActionRef("");
      setExpr("0 9 * * *");
      setEnabled(true);
      setDryRunResult(null);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "저장 중 오류가 발생했어요.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="새 Cron 잡"
      description="저장 전에 ``Dry-run`` 으로 표현식과 다음 실행 시각을 확인하세요."
      size="wide"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose} disabled={submitting}>
            취소
          </Button>
          <Button variant="secondary" size="sm" onClick={handleDryRun} disabled={!exprValid}>
            Dry-run
          </Button>
          <Button variant="primary" size="sm" onClick={handleSubmit} disabled={!ready}>
            {submitting ? "저장 중…" : "저장"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-5">
        <Field
          label="이름"
          helper="영문 소문자·숫자·`_`·`-` 만 사용. 최대 64자."
          error={
            name.length > 0 && !nameValid
              ? "이름 형식이 올바르지 않아요 (소문자·숫자·`_`·`-`, 최대 64자)."
              : null
          }
        >
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            invalid={name.length > 0 && !nameValid}
            placeholder="예: morning-brief"
          />
        </Field>

        <Field label="표현식" helper="다음 실행 시각이 자동으로 미리 계산돼요.">
          <ExpressionInput value={expr} onChange={setExpr} onValidityChange={setExprValid} />
        </Field>

        <Field label="액션">
          <div className="flex items-center gap-2">
            <select
              value={actionType}
              onChange={(e) => setActionType(e.target.value as CronActionType)}
              className="rounded-(--radius-m) border border-(--border) bg-(--card) px-3 py-2 text-sm text-(--foreground)"
            >
              <option value="prompt">prompt</option>
              <option value="recipe">recipe</option>
            </select>
            <Input
              value={actionRef}
              onChange={(e) => setActionRef(e.target.value)}
              placeholder={
                actionType === "prompt"
                  ? "에이전트에게 보낼 메시지 템플릿"
                  : "레시피 ID (예: weekly_digest)"
              }
              invalid={actionRef.length > 0 && !refValid}
            />
          </div>
        </Field>

        <Field label="활성화">
          <div className="flex items-center gap-2">
            <Switch
              checked={enabled}
              onCheckedChange={setEnabled}
              label="저장 후 즉시 활성"
            />
            <span className="text-sm text-(--muted-foreground)">
              {enabled ? "저장 후 즉시 스케줄 등록" : "비활성 상태로 저장"}
            </span>
          </div>
        </Field>

        {dryRunResult ? (
          <div className="rounded-(--radius-m) border border-(--border) bg-(--surface) px-3 py-2 text-xs text-(--foreground)">
            {dryRunResult}
          </div>
        ) : null}

        {error ? (
          <div className="rounded-(--radius-m) border border-transparent bg-(--color-error-bg) px-3 py-2 text-xs text-(--color-error)">
            {error}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}

function Field({
  label,
  helper,
  error,
  children,
}: {
  label: string;
  helper?: string;
  error?: string | null;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-sm font-medium text-(--foreground-strong)">{label}</label>
      {children}
      {error ? (
        <span className="text-xs text-(--color-error)">{error}</span>
      ) : helper ? (
        <span className="text-xs text-(--muted-foreground)">{helper}</span>
      ) : null}
    </div>
  );
}
