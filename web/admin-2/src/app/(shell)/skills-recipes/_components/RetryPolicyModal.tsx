"use client";

/**
 * RetryPolicyModal — admin.pen `GnNLO` 의 BIZ-109 P1 신규 (재시도 정책 인라인 편집).
 *
 * 폼 필드:
 *  - 최대 시도 횟수 (정수, ≥1)
 *  - 백오프 전략 (none/fixed/linear/exponential)
 *  - 초기 백오프 (초, ≥0) — 전략이 none 이면 disabled
 *  - 단일 시도 타임아웃 (초, ≥1)
 *
 * llm-router 의 EditProviderModal 패턴 그대로 — open + skill 으로 prefill,
 * 검증 실패 시 저장 버튼 disabled.
 */

import { useEffect, useState } from "react";
import { Button } from "@/design/atoms/Button";
import { Input } from "@/design/atoms/Input";
import { Label } from "@/design/atoms/Label";
import { Select } from "@/design/atoms/Select";
import type {
  BackoffStrategy,
  InstalledSkill,
  RetryPolicy,
} from "../_data";
import { Modal } from "./Modal";

interface RetryPolicyModalProps {
  open: boolean;
  /** 편집 대상 — null 이면 modal 은 닫힌 상태. */
  skill: InstalledSkill | null;
  onClose: () => void;
  /** 검증 통과 후 호출. 부모가 fixture/state 갱신을 담당. */
  onSubmit: (skillId: string, policy: RetryPolicy) => void;
}

const STRATEGY_OPTIONS = [
  { value: "none", label: "재시도 없음 (none)" },
  { value: "fixed", label: "고정 (fixed)" },
  { value: "linear", label: "선형 증가 (linear)" },
  { value: "exponential", label: "지수 증가 (exponential)" },
];

export function RetryPolicyModal({
  open,
  skill,
  onClose,
  onSubmit,
}: RetryPolicyModalProps) {
  const [policy, setPolicy] = useState<RetryPolicy | null>(null);
  const [submitted, setSubmitted] = useState(false);

  // open 또는 skill 이 바뀌면 폼을 다시 prefill.
  useEffect(() => {
    if (skill) {
      setPolicy({ ...skill.retryPolicy });
      setSubmitted(false);
    }
  }, [skill, open]);

  // skill=null 또는 internal state 미초기화 시에는 modal 자체를 닫힌 상태로 유지.
  if (!open || !skill || !policy) {
    return (
      <Modal open={false} onClose={onClose} title={null} footer={null}>
        {null}
      </Modal>
    );
  }

  const errors = validate(policy);
  const valid = Object.keys(errors).length === 0;
  const showErrors = submitted;

  const handleSubmit = () => {
    setSubmitted(true);
    if (!valid) return;
    onSubmit(skill.id, policy);
    onClose();
  };

  const isStrategyNone = policy.backoffStrategy === "none";

  return (
    <Modal
      open={open}
      onClose={onClose}
      data-testid="retry-policy-modal"
      title={
        <div className="flex flex-col gap-0.5">
          <h2 className="text-lg font-semibold text-(--foreground-strong)">
            재시도 정책 편집
          </h2>
          <p className="text-xs text-(--muted-foreground)">
            <span className="font-mono">{skill.name}</span> 의 시도 횟수·백오프·타임아웃을 변경합니다.
          </p>
        </div>
      }
      footer={
        <>
          <Button
            variant="ghost"
            onClick={onClose}
            data-testid="retry-policy-cancel"
          >
            취소
          </Button>
          <Button
            variant="primary"
            onClick={handleSubmit}
            disabled={showErrors && !valid}
            data-testid="retry-policy-submit"
          >
            저장
          </Button>
        </>
      }
    >
      <Field
        id="retry-policy-max-attempts"
        label="최대 시도 횟수"
        error={showErrors ? errors.maxAttempts : undefined}
      >
        <Input
          id="retry-policy-max-attempts"
          type="number"
          min={1}
          value={policy.maxAttempts}
          onChange={(e) =>
            setPolicy({
              ...policy,
              maxAttempts: Number(e.currentTarget.value),
            })
          }
          error={showErrors && Boolean(errors.maxAttempts)}
          data-testid="retry-policy-max-attempts"
        />
        <p className="text-xs text-(--muted-foreground)">
          1 = 재시도 없음 — 한 번만 실행합니다.
        </p>
      </Field>

      <Field id="retry-policy-strategy" label="백오프 전략">
        <Select
          id="retry-policy-strategy"
          options={STRATEGY_OPTIONS}
          value={policy.backoffStrategy}
          onChange={(e) =>
            setPolicy({
              ...policy,
              backoffStrategy: e.currentTarget.value as BackoffStrategy,
            })
          }
          data-testid="retry-policy-strategy"
        />
      </Field>

      <Field
        id="retry-policy-backoff"
        label="초기 백오프 (초)"
        error={showErrors ? errors.backoffSeconds : undefined}
      >
        <Input
          id="retry-policy-backoff"
          type="number"
          min={0}
          step="0.5"
          value={policy.backoffSeconds}
          onChange={(e) =>
            setPolicy({
              ...policy,
              backoffSeconds: Number(e.currentTarget.value),
            })
          }
          disabled={isStrategyNone}
          error={showErrors && Boolean(errors.backoffSeconds)}
          data-testid="retry-policy-backoff"
        />
        {isStrategyNone ? (
          <p className="text-xs text-(--muted-foreground)">
            전략이 &quot;재시도 없음&quot; 이라 이 값은 사용되지 않습니다.
          </p>
        ) : null}
      </Field>

      <Field
        id="retry-policy-timeout"
        label="단일 시도 타임아웃 (초)"
        error={showErrors ? errors.timeoutSeconds : undefined}
      >
        <Input
          id="retry-policy-timeout"
          type="number"
          min={1}
          value={policy.timeoutSeconds}
          onChange={(e) =>
            setPolicy({
              ...policy,
              timeoutSeconds: Number(e.currentTarget.value),
            })
          }
          error={showErrors && Boolean(errors.timeoutSeconds)}
          data-testid="retry-policy-timeout"
        />
      </Field>
    </Modal>
  );
}

/** 폼 검증 — 음수·소수점 시도 횟수, 음수 백오프, 비양수 타임아웃을 거른다. */
export function validate(policy: RetryPolicy): Record<string, string> {
  const errors: Record<string, string> = {};
  if (
    !Number.isFinite(policy.maxAttempts) ||
    policy.maxAttempts < 1 ||
    !Number.isInteger(policy.maxAttempts)
  ) {
    errors.maxAttempts = "1 이상의 정수를 입력하세요.";
  }
  // 백오프는 전략이 none 이 아닐 때만 검증 — none 일 때는 항상 사용되지 않으므로 통과.
  if (
    policy.backoffStrategy !== "none" &&
    (!Number.isFinite(policy.backoffSeconds) || policy.backoffSeconds < 0)
  ) {
    errors.backoffSeconds = "0 이상의 숫자를 입력하세요.";
  }
  if (
    !Number.isFinite(policy.timeoutSeconds) ||
    policy.timeoutSeconds < 1 ||
    !Number.isInteger(policy.timeoutSeconds)
  ) {
    errors.timeoutSeconds = "1 이상의 정수(초) 를 입력하세요.";
  }
  return errors;
}

interface FieldProps {
  id: string;
  label: string;
  error?: string;
  children: React.ReactNode;
}

function Field({ id, label, error, children }: FieldProps) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {error ? (
        <p
          className="text-xs text-(--color-error)"
          data-testid={`${id}-error`}
          role="alert"
        >
          {error}
        </p>
      ) : null}
    </div>
  );
}
