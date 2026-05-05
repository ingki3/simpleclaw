"use client";

/**
 * EditProviderModal — admin.pen `AzGck` (Edit Provider) 박제.
 *
 * Add 와 다른 점:
 *  - 기존 provider 값 prefill (name 은 헤더로만 노출, 변경 불가).
 *  - SecretField (현 마스킹) 옆에 회전(rotate) 버튼.
 *  - Fallback 토글 + 우선순위 입력.
 *  - 좌하단 destructive "프로바이더 삭제" 액션.
 */

import { useEffect, useState } from "react";
import { Button } from "@/design/atoms/Button";
import { Input } from "@/design/atoms/Input";
import { Label } from "@/design/atoms/Label";
import { Switch } from "@/design/atoms/Switch";
import { SecretField } from "@/design/atoms/SecretField";
import type { RouterProvider } from "../_data";
import { Modal } from "./Modal";

export interface EditProviderFormValue {
  id: string;
  model: string;
  timeoutMs: number;
  fallbackPriority: number;
  inFallbackChain: boolean;
}

interface EditProviderModalProps {
  open: boolean;
  /** 편집 대상 — null 이면 modal 은 닫힌 상태. */
  provider: RouterProvider | null;
  onClose: () => void;
  onSubmit: (value: EditProviderFormValue) => void;
  /** 시크릿 회전 — 부모가 새 마스킹 prefix 를 응답으로 갱신. */
  onRotateSecret?: (providerId: string) => void;
  /** 프로바이더 삭제 (좌하단 destructive). */
  onDelete?: (providerId: string) => void;
}

export function EditProviderModal({
  open,
  provider,
  onClose,
  onSubmit,
  onRotateSecret,
  onDelete,
}: EditProviderModalProps) {
  const [form, setForm] = useState<EditProviderFormValue | null>(null);
  const [submitted, setSubmitted] = useState(false);

  // open 또는 provider 가 바뀌면 폼을 다시 prefill — Add modal 과 달리
  // controlled state 가 외부 entity 에 묶여있다.
  useEffect(() => {
    if (provider) {
      setForm({
        id: provider.id,
        model: provider.model,
        timeoutMs: 30000,
        fallbackPriority:
          provider.fallbackPriority !== null ? provider.fallbackPriority + 1 : 1,
        inFallbackChain: provider.inFallbackChain,
      });
      setSubmitted(false);
    }
  }, [provider, open]);

  if (!provider || !form) {
    // Modal 은 open=false 일 때 null 반환하므로 안전.
    return (
      <Modal
        open={false}
        onClose={onClose}
        title={null}
        footer={null}
      >
        {null}
      </Modal>
    );
  }

  const errors = validate(form);
  const showErrors = submitted;
  const valid = Object.keys(errors).length === 0;

  const handleSubmit = () => {
    setSubmitted(true);
    if (!valid) return;
    onSubmit(form);
    onClose();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      data-testid="edit-provider-modal"
      title={
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold text-(--foreground-strong)">
            프로바이더 편집 — {provider.name}
          </h2>
          <span
            data-testid="edit-provider-keyring"
            className="rounded-(--radius-sm) bg-(--surface) px-1.5 py-0.5 font-mono text-[11px] text-(--muted-foreground)"
          >
            keyring: {provider.keyringName}
          </span>
        </div>
      }
      footerLeft={
        onDelete ? (
          <Button
            variant="destructive"
            size="sm"
            onClick={() => {
              onDelete(provider.id);
              onClose();
            }}
            data-testid="edit-provider-delete"
          >
            프로바이더 삭제
          </Button>
        ) : null
      }
      footer={
        <>
          <Button
            variant="ghost"
            onClick={onClose}
            data-testid="edit-provider-cancel"
          >
            취소
          </Button>
          <Button
            variant="primary"
            onClick={handleSubmit}
            disabled={showErrors && !valid}
            data-testid="edit-provider-submit"
          >
            저장
          </Button>
        </>
      }
    >
      <Field id="edit-provider-model" label="모델">
        <Input
          id="edit-provider-model"
          value={form.model}
          onChange={(e) => setForm({ ...form, model: e.currentTarget.value })}
          data-testid="edit-provider-model"
        />
      </Field>

      <div className="flex flex-col gap-1.5">
        <Label>API Key</Label>
        <div className="flex items-center gap-2">
          <SecretField
            maskedPreview={provider.apiKeyMasked}
            onReveal={() => {
              /* mock — 데몬 통합 단계에서 실제 fetch. */
            }}
            onCopy={() => {
              if (typeof navigator !== "undefined" && navigator.clipboard) {
                void navigator.clipboard.writeText(provider.apiKeyMasked);
              }
            }}
          />
          {onRotateSecret ? (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => onRotateSecret(provider.id)}
              data-testid="edit-provider-rotate"
            >
              ↻ 회전
            </Button>
          ) : null}
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field
          id="edit-provider-timeout"
          label="Timeout (ms)"
          error={showErrors ? errors.timeoutMs : undefined}
        >
          <Input
            id="edit-provider-timeout"
            type="number"
            min={1}
            value={form.timeoutMs}
            onChange={(e) =>
              setForm({ ...form, timeoutMs: Number(e.currentTarget.value) })
            }
            error={showErrors && Boolean(errors.timeoutMs)}
            data-testid="edit-provider-timeout"
          />
        </Field>
        <Field
          id="edit-provider-priority"
          label="우선순위"
          error={showErrors ? errors.fallbackPriority : undefined}
        >
          <Input
            id="edit-provider-priority"
            type="number"
            min={1}
            value={form.fallbackPriority}
            onChange={(e) =>
              setForm({
                ...form,
                fallbackPriority: Number(e.currentTarget.value),
              })
            }
            error={showErrors && Boolean(errors.fallbackPriority)}
            data-testid="edit-provider-priority"
          />
        </Field>
      </div>

      <div className="flex items-center justify-between gap-3 rounded-(--radius-m) border border-(--border) bg-(--surface) px-3 py-2.5">
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-medium text-(--foreground)">
            ⛓ Fallback 체인 포함
          </span>
          <span className="text-xs text-(--muted-foreground)">
            이 프로바이더 장애 시 다음 우선순위로 자동 라우팅
          </span>
        </div>
        <Switch
          checked={form.inFallbackChain}
          onCheckedChange={(next) =>
            setForm({ ...form, inFallbackChain: next })
          }
          label="Fallback 체인 포함"
          data-testid="edit-provider-fallback"
        />
      </div>
    </Modal>
  );
}

function validate(form: EditProviderFormValue): Record<string, string> {
  const errors: Record<string, string> = {};
  if (
    !Number.isFinite(form.timeoutMs) ||
    form.timeoutMs <= 0 ||
    !Number.isInteger(form.timeoutMs)
  ) {
    errors.timeoutMs = "양의 정수(ms) 를 입력하세요.";
  }
  if (
    !Number.isFinite(form.fallbackPriority) ||
    form.fallbackPriority <= 0 ||
    !Number.isInteger(form.fallbackPriority)
  ) {
    errors.fallbackPriority = "1 이상의 정수를 입력하세요.";
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
