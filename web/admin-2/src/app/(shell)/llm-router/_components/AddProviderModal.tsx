"use client";

/**
 * AddProviderModal — admin.pen `oUzPN` (Add Provider) 박제.
 *
 * 폼 필드: 프로바이더 이름 / API Type / API Key / Base URL / 모델 / Timeout / Fallback toggle.
 * 검증:
 *  - 이름: 1자 이상 (영문/숫자/dash/underscore).
 *  - API Key: 1자 이상.
 *  - Timeout: 양의 정수 ms.
 * 검증 실패 시 해당 필드에 error tone, 저장 버튼은 disabled.
 */

import { useState, type FormEvent } from "react";
import { Button } from "@/design/atoms/Button";
import { Input } from "@/design/atoms/Input";
import { Label } from "@/design/atoms/Label";
import { Select } from "@/design/atoms/Select";
import { Switch } from "@/design/atoms/Switch";
import { Modal } from "./Modal";

export interface AddProviderFormValue {
  name: string;
  apiType: "anthropic" | "openai" | "gemini" | "custom";
  apiKey: string;
  baseUrl: string;
  model: string;
  timeoutMs: number;
  inFallbackChain: boolean;
}

interface AddProviderModalProps {
  open: boolean;
  onClose: () => void;
  /** 검증 통과 후 호출. 부모가 fixture/state 갱신을 담당. */
  onSubmit: (value: AddProviderFormValue) => void;
}

const API_TYPE_OPTIONS = [
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "gemini", label: "Gemini" },
  { value: "custom", label: "Custom (OpenAI-compatible)" },
];

const DEFAULT_FORM: AddProviderFormValue = {
  name: "",
  apiType: "openai",
  apiKey: "",
  baseUrl: "https://api.openai.com/v1",
  model: "",
  timeoutMs: 30000,
  inFallbackChain: true,
};

export function AddProviderModal({
  open,
  onClose,
  onSubmit,
}: AddProviderModalProps) {
  const [form, setForm] = useState<AddProviderFormValue>(DEFAULT_FORM);
  const [submitted, setSubmitted] = useState(false);

  // 검증 — submit 시도 후에만 메시지를 노출(첫 진입 시 빈 칸을 빨강 처리하지 않기 위함).
  const errors = validate(form);
  const showErrors = submitted;
  const valid = Object.keys(errors).length === 0;

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    if (!valid) return;
    onSubmit(form);
    setForm(DEFAULT_FORM);
    setSubmitted(false);
    onClose();
  };

  const handleClose = () => {
    setForm(DEFAULT_FORM);
    setSubmitted(false);
    onClose();
  };

  return (
    <Modal
      open={open}
      onClose={handleClose}
      data-testid="add-provider-modal"
      title={
        <div className="flex flex-col gap-0.5">
          <h2 className="text-lg font-semibold text-(--foreground-strong)">
            프로바이더 추가
          </h2>
          <p className="text-xs text-(--muted-foreground)">
            새 LLM 프로바이더를 등록합니다. 시크릿은 keyring 에 저장됩니다.
          </p>
        </div>
      }
      footer={
        <>
          <Button
            variant="ghost"
            onClick={handleClose}
            data-testid="add-provider-cancel"
          >
            취소
          </Button>
          <Button
            variant="primary"
            onClick={handleSubmit}
            disabled={showErrors && !valid}
            data-testid="add-provider-submit"
          >
            추가
          </Button>
        </>
      }
    >
      <form
        onSubmit={handleSubmit}
        className="flex flex-col gap-4"
        data-testid="add-provider-form"
      >
        <Field
          id="add-provider-name"
          label="프로바이더 이름"
          required
          error={showErrors ? errors.name : undefined}
        >
          <Input
            id="add-provider-name"
            value={form.name}
            placeholder="예: my-openai"
            onChange={(e) => setForm({ ...form, name: e.currentTarget.value })}
            error={showErrors && Boolean(errors.name)}
            data-testid="add-provider-name"
          />
        </Field>

        <Field id="add-provider-api-type" label="API Type" required>
          <Select
            id="add-provider-api-type"
            options={API_TYPE_OPTIONS}
            value={form.apiType}
            onChange={(e) =>
              setForm({
                ...form,
                apiType: e.currentTarget
                  .value as AddProviderFormValue["apiType"],
              })
            }
            data-testid="add-provider-api-type"
          />
        </Field>

        <Field
          id="add-provider-api-key"
          label="API Key"
          required
          error={showErrors ? errors.apiKey : undefined}
        >
          <Input
            id="add-provider-api-key"
            type="password"
            value={form.apiKey}
            placeholder="sk-••••••••••••••••"
            onChange={(e) =>
              setForm({ ...form, apiKey: e.currentTarget.value })
            }
            error={showErrors && Boolean(errors.apiKey)}
            data-testid="add-provider-api-key"
          />
        </Field>

        <Field id="add-provider-base-url" label="Base URL">
          <Input
            id="add-provider-base-url"
            value={form.baseUrl}
            placeholder="https://api.openai.com/v1"
            onChange={(e) =>
              setForm({ ...form, baseUrl: e.currentTarget.value })
            }
            data-testid="add-provider-base-url"
          />
        </Field>

        <div className="grid gap-3 sm:grid-cols-2">
          <Field id="add-provider-model" label="모델 이름">
            <Input
              id="add-provider-model"
              value={form.model}
              placeholder="gpt-4o"
              onChange={(e) =>
                setForm({ ...form, model: e.currentTarget.value })
              }
              data-testid="add-provider-model"
            />
          </Field>
          <Field
            id="add-provider-timeout"
            label="Timeout"
            error={showErrors ? errors.timeoutMs : undefined}
          >
            <Input
              id="add-provider-timeout"
              type="number"
              min={1}
              value={form.timeoutMs}
              onChange={(e) =>
                setForm({
                  ...form,
                  timeoutMs: Number(e.currentTarget.value),
                })
              }
              trailing={<span className="text-xs">ms</span>}
              error={showErrors && Boolean(errors.timeoutMs)}
              data-testid="add-provider-timeout"
            />
          </Field>
        </div>

        <div className="flex items-center justify-between gap-3 rounded-(--radius-m) border border-(--border) bg-(--surface) px-3 py-2.5">
          <div className="flex flex-col gap-0.5">
            <span className="text-sm font-medium text-(--foreground)">
              Fallback 체인에 추가
            </span>
            <span className="text-xs text-(--muted-foreground)">
              장애 시 자동으로 이 프로바이더로 전환합니다
            </span>
          </div>
          <Switch
            checked={form.inFallbackChain}
            onCheckedChange={(next) =>
              setForm({ ...form, inFallbackChain: next })
            }
            label="Fallback 체인에 추가"
            data-testid="add-provider-fallback"
          />
        </div>
      </form>
    </Modal>
  );
}

function validate(form: AddProviderFormValue): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!/^[a-zA-Z0-9_-]+$/.test(form.name.trim())) {
    errors.name =
      "영문/숫자/대시(-)/언더스코어(_)만 사용할 수 있습니다.";
  }
  if (form.apiKey.trim().length === 0) {
    errors.apiKey = "API Key 를 입력하세요.";
  }
  if (
    !Number.isFinite(form.timeoutMs) ||
    form.timeoutMs <= 0 ||
    !Number.isInteger(form.timeoutMs)
  ) {
    errors.timeoutMs = "양의 정수(ms) 를 입력하세요.";
  }
  return errors;
}

interface FieldProps {
  id: string;
  label: string;
  required?: boolean;
  error?: string;
  children: React.ReactNode;
}

function Field({ id, label, required, error, children }: FieldProps) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id} required={required}>
        {label}
      </Label>
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
