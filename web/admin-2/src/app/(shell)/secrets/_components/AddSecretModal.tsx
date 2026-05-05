"use client";

/**
 * AddSecretModal — admin.pen `Nm9nU` (Add Secret) 박제.
 *
 * 입력:
 *  - 키 이름 (`scope.name` 형식, lowercase + 숫자 + `._-`, 3..64 자)
 *  - scope (LLM 프로바이더 / 채널 / 시스템 / 외부 서비스)
 *  - 시크릿 값 (SecretField atom — 마스킹 + reveal 30s)
 *  - 회전 정책 (PolicyChip 으로 시각화 — Hot / Service-restart / Process-restart)
 *  - 메모 (선택)
 *
 * 보안 경계:
 *  - 시크릿 값은 input 의 onChange 외에는 어디에도 흘리지 않는다 — 콘솔/네트워크
 *    박제는 *값 길이* 만 노출 (DoD).
 *  - 부모(page) 의 onSubmit 에 평문이 전달되더라도, 부모는 해당 값을 fixture
 *    state 에 *저장하지 않고* maskedPreview 만 사전계산해 추가한다.
 *
 * 검증 실패 시 저장 버튼 disabled — submit 후 첫 검증 실행, 그 전에는 placeholder
 * 만 노출 (RetryPolicyModal 패턴).
 */

import { useEffect, useState } from "react";
import { Button } from "@/design/atoms/Button";
import { Input } from "@/design/atoms/Input";
import { Label } from "@/design/atoms/Label";
import { Select } from "@/design/atoms/Select";
import { Textarea } from "@/design/atoms/Textarea";
import { PolicyChip, type PolicyKind } from "@/design/molecules/PolicyChip";
import {
  KEY_NAME_PATTERN,
  SCOPE_LABEL,
  type SecretScope,
} from "../_data";
import { Modal } from "./Modal";

export interface AddSecretInput {
  keyName: string;
  scope: SecretScope;
  /** 평문 — 부모는 maskedPreview 만 사전계산해 fixture 에 추가하고 즉시 폐기한다. */
  value: string;
  policy: PolicyKind;
  note: string;
}

interface AddSecretModalProps {
  open: boolean;
  onClose: () => void;
  /** 사용 가능한 scope — 보통 fixture 의 SCOPES 그대로. */
  scopes: readonly SecretScope[];
  /** 이미 존재하는 키 이름 목록 — 중복 검증용. */
  existingKeyNames: readonly string[];
  /** 검증 통과 후 호출. 부모가 fixture state 갱신 + 토스트 박제. */
  onSubmit: (input: AddSecretInput) => void;
}

const POLICY_OPTIONS: ReadonlyArray<{
  value: PolicyKind;
  label: string;
  description: string;
}> = [
  {
    value: "hot",
    label: "Hot",
    description: "회전 즉시 라우터/채널이 사용. 재시작 없음.",
  },
  {
    value: "service-restart",
    label: "Service-restart",
    description: "회전 후 해당 서비스(채널/봇 등) 재시작 필요.",
  },
  {
    value: "process-restart",
    label: "Process-restart",
    description: "회전 후 데몬 전체 재시작 필요 — 다운타임 발생.",
  },
];

export function AddSecretModal({
  open,
  onClose,
  scopes,
  existingKeyNames,
  onSubmit,
}: AddSecretModalProps) {
  const [keyName, setKeyName] = useState("");
  const [scope, setScope] = useState<SecretScope>(scopes[0] ?? "system");
  const [value, setValue] = useState("");
  const [policy, setPolicy] = useState<PolicyKind>("hot");
  const [note, setNote] = useState("");
  const [revealed, setRevealed] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  // open 토글 시마다 폼 초기화 — 이전 모달의 시크릿 값이 메모리에 잔존하지 않도록.
  useEffect(() => {
    if (open) {
      setKeyName("");
      setScope(scopes[0] ?? "system");
      setValue("");
      setPolicy("hot");
      setNote("");
      setRevealed(false);
      setSubmitted(false);
    }
  }, [open, scopes]);

  const errors = validate({ keyName, value }, existingKeyNames);
  const valid = Object.keys(errors).length === 0;
  const showErrors = submitted;

  const handleSubmit = () => {
    setSubmitted(true);
    if (!valid) return;
    onSubmit({
      keyName: keyName.trim(),
      scope,
      value,
      policy,
      note: note.trim(),
    });
    onClose();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      width="md"
      data-testid="add-secret-modal"
      title={
        <div className="flex flex-col gap-0.5">
          <h2 className="text-lg font-semibold text-(--foreground-strong)">
            시크릿 추가
          </h2>
          <p className="text-xs text-(--muted-foreground)">
            keyring 에 새 키를 등록합니다 — 평문은 저장 직후 마스킹됩니다.
          </p>
        </div>
      }
      footer={
        <>
          <Button
            variant="ghost"
            onClick={onClose}
            data-testid="add-secret-cancel"
          >
            취소
          </Button>
          <Button
            variant="primary"
            onClick={handleSubmit}
            disabled={showErrors && !valid}
            data-testid="add-secret-submit"
          >
            저장
          </Button>
        </>
      }
    >
      <Field
        id="add-secret-key-name"
        label="키 이름"
        error={showErrors ? errors.keyName : undefined}
      >
        <Input
          id="add-secret-key-name"
          autoFocus
          value={keyName}
          onChange={(e) => setKeyName(e.currentTarget.value)}
          placeholder="예: llm.anthropic_api_key"
          autoComplete="off"
          spellCheck={false}
          error={showErrors && Boolean(errors.keyName)}
          data-testid="add-secret-key-name"
        />
        <p className="text-xs text-(--muted-foreground)">
          소문자·숫자·`. _ -` 만, 3–64 자. 보통 <code>scope.name</code> 형태.
        </p>
      </Field>

      <Field id="add-secret-scope" label="카테고리">
        <Select
          id="add-secret-scope"
          value={scope}
          onChange={(e) => setScope(e.currentTarget.value as SecretScope)}
          options={scopes.map((s) => ({
            value: s,
            label: SCOPE_LABEL[s],
          }))}
          data-testid="add-secret-scope"
        />
      </Field>

      <Field
        id="add-secret-value"
        label="시크릿 값"
        error={showErrors ? errors.value : undefined}
      >
        {/*
          전용 SecretField 가 아닌 password input 을 직접 사용 — 사용자가 직접
          타이핑하는 입력에는 SecretField (마스킹 표시 + reveal 카운트다운) 가
          맞지 않음. reveal 토글은 type 만 password ↔ text 로 전환하고, 평문은
          어디에도 복사하지 않는다.
        */}
        <Input
          id="add-secret-value"
          type={revealed ? "text" : "password"}
          value={value}
          onChange={(e) => setValue(e.currentTarget.value)}
          placeholder="sk-..."
          autoComplete="new-password"
          spellCheck={false}
          error={showErrors && Boolean(errors.value)}
          data-testid="add-secret-value"
          trailing={
            <button
              type="button"
              onClick={() => setRevealed((v) => !v)}
              className="rounded-(--radius-sm) px-1.5 py-0.5 text-xs text-(--primary) hover:bg-(--primary-tint)"
              data-testid="add-secret-reveal"
            >
              {revealed ? "가리기" : "보기"}
            </button>
          }
        />
        <p className="text-xs text-(--muted-foreground)">
          저장 후에는 마지막 4자리 미리보기만 표시됩니다. 평문은 다시 볼 수 없어요.
        </p>
      </Field>

      <Field id="add-secret-policy" label="회전 정책">
        <div
          role="radiogroup"
          aria-labelledby="add-secret-policy-label"
          data-testid="add-secret-policy"
          className="flex flex-col gap-2"
        >
          {POLICY_OPTIONS.map((opt) => {
            const checked = policy === opt.value;
            return (
              <label
                key={opt.value}
                className="flex cursor-pointer items-start gap-2 rounded-(--radius-m) border border-(--border) bg-(--card) p-3 hover:bg-(--surface)"
                data-checked={checked || undefined}
                data-testid={`add-secret-policy-${opt.value}`}
              >
                <input
                  type="radio"
                  name="add-secret-policy"
                  value={opt.value}
                  checked={checked}
                  onChange={() => setPolicy(opt.value)}
                  className="mt-1"
                />
                <span className="flex flex-col gap-1">
                  <PolicyChip kind={opt.value} />
                  <span className="text-xs text-(--muted-foreground)">
                    {opt.description}
                  </span>
                </span>
              </label>
            );
          })}
        </div>
      </Field>

      <Field id="add-secret-note" label="메모 (선택)">
        <Textarea
          id="add-secret-note"
          value={note}
          onChange={(e) => setNote(e.currentTarget.value)}
          rows={2}
          placeholder="예: Claude Opus 4.6 라우터 default."
          data-testid="add-secret-note"
        />
      </Field>
    </Modal>
  );
}

interface ValidateInput {
  keyName: string;
  value: string;
}

/**
 * 폼 검증 — 키 이름 패턴/중복 + 시크릿 값 비어있음.
 *
 * 시크릿 값은 길이 0 만 검증 — 길이/엔트로피 검증은 백엔드 keyring 정책 책임.
 */
export function validate(
  input: ValidateInput,
  existingKeyNames: readonly string[],
): Record<string, string> {
  const errors: Record<string, string> = {};
  const trimmed = input.keyName.trim();
  if (!trimmed) {
    errors.keyName = "키 이름을 입력하세요.";
  } else if (!KEY_NAME_PATTERN.test(trimmed)) {
    errors.keyName =
      "소문자·숫자·`. _ -` 만 사용할 수 있고, 3–64 자여야 합니다.";
  } else if (existingKeyNames.includes(trimmed)) {
    errors.keyName = "이미 같은 이름의 키가 있어요.";
  }
  if (!input.value) {
    errors.value = "시크릿 값을 입력하세요.";
  }
  return errors;
}

/** 평문 시크릿 → maskedPreview 사전계산 (`••••<last4>`). */
export function maskValue(value: string): string {
  const last4 = value.slice(-4) || "0000";
  return `••••${last4}`;
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
