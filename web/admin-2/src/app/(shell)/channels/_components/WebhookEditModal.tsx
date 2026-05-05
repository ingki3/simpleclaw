"use client";

/**
 * WebhookEditModal — admin.pen `Ut000` (Webhook Edit modal) 를 React 로 박제.
 *
 * 폼 필드:
 *  - URL — endpoint 의 inbound URL.
 *  - 서명 시크릿 — env variable 명 (예: `WEBHOOK_SLACK_SECRET`). 마스킹은
 *    채널 카드의 secret URI 표기와 동일하게 코드 톤으로.
 *  - Rate Limit (req/s) / 동시성 — endpoint 별 정책.
 *  - Body 스키마 (JSON Schema) — 텍스트 영역. 본 단계는 자유 텍스트로 받고,
 *    검증은 *비빈* + 합리적 길이 체크만.
 *
 * footerLeft 에 "트래픽 시뮬레이션" 버튼 — 부모가 TrafficSimulationModal 을 연다.
 * 검증 실패 시 저장 버튼 disabled.
 */

import { useEffect, useState } from "react";
import { Button } from "@/design/atoms/Button";
import { Input } from "@/design/atoms/Input";
import { Label } from "@/design/atoms/Label";
import { Textarea } from "@/design/atoms/Textarea";
import type { WebhookEndpoint } from "../_data";
import { Modal } from "./Modal";

interface WebhookEditModalProps {
  open: boolean;
  /** 편집 대상 — null 이면 modal 은 닫힌 상태. */
  endpoint: WebhookEndpoint | null;
  onClose: () => void;
  /** 검증 통과 후 호출 — 부모가 fixture/state 갱신. */
  onSubmit: (id: string, next: WebhookEndpoint) => void;
  /** 좌하단 "트래픽 시뮬레이션" 버튼 — 부모가 TrafficSimulationModal 을 연다. */
  onOpenSimulation: (endpoint: WebhookEndpoint) => void;
}

export function WebhookEditModal({
  open,
  endpoint,
  onClose,
  onSubmit,
  onOpenSimulation,
}: WebhookEditModalProps) {
  const [draft, setDraft] = useState<WebhookEndpoint | null>(null);
  const [submitted, setSubmitted] = useState(false);

  // open 또는 endpoint 가 바뀌면 폼을 다시 prefill.
  useEffect(() => {
    if (open && endpoint) {
      setDraft({ ...endpoint });
      setSubmitted(false);
    }
  }, [open, endpoint]);

  // endpoint=null 일 때는 modal 자체를 닫힌 상태로 유지 — Modal 컴포넌트가
  // open=false 면 DOM 미렌더이므로 빈 자리표지자 패턴.
  if (!open || !endpoint || !draft) {
    return (
      <Modal open={false} onClose={onClose} title={null} footer={null}>
        {null}
      </Modal>
    );
  }

  const errors = validate(draft);
  const valid = Object.keys(errors).length === 0;
  const showErrors = submitted;

  const handleSubmit = () => {
    setSubmitted(true);
    if (!valid) return;
    onSubmit(endpoint.id, draft);
    onClose();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      width="md"
      data-testid="webhook-edit-modal"
      title={
        <div className="flex flex-col gap-0.5">
          <h2 className="text-lg font-semibold text-(--foreground-strong)">
            웹훅 편집 — <span className="font-mono">{endpoint.id}</span>
          </h2>
          <p className="text-xs text-(--muted-foreground)">
            URL · 시크릿 · rate limit · 동시성 · Body 스키마를 변경합니다.
          </p>
        </div>
      }
      footerLeft={
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onOpenSimulation(draft)}
          data-testid="webhook-edit-simulate"
          leftIcon={<span aria-hidden>∿</span>}
        >
          트래픽 시뮬레이션
        </Button>
      }
      footer={
        <>
          <Button
            variant="ghost"
            onClick={onClose}
            data-testid="webhook-edit-cancel"
          >
            취소
          </Button>
          <Button
            variant="primary"
            onClick={handleSubmit}
            disabled={showErrors && !valid}
            data-testid="webhook-edit-submit"
          >
            저장
          </Button>
        </>
      }
    >
      <Field
        id="webhook-edit-url"
        label="URL"
        error={showErrors ? errors.url : undefined}
      >
        <Input
          id="webhook-edit-url"
          value={draft.url}
          onChange={(e) =>
            setDraft({ ...draft, url: e.currentTarget.value })
          }
          placeholder="https://hooks.simpleclaw.dev/..."
          error={showErrors && Boolean(errors.url)}
          data-testid="webhook-edit-url"
        />
      </Field>

      <Field
        id="webhook-edit-secret"
        label="서명 시크릿"
        error={showErrors ? errors.secretEnv : undefined}
      >
        <Input
          id="webhook-edit-secret"
          value={draft.secretEnv}
          onChange={(e) =>
            setDraft({ ...draft, secretEnv: e.currentTarget.value })
          }
          placeholder="WEBHOOK_FOO_SECRET"
          trailing={<span className="font-mono text-xs">env</span>}
          error={showErrors && Boolean(errors.secretEnv)}
          data-testid="webhook-edit-secret"
        />
      </Field>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field
          id="webhook-edit-rate-limit"
          label="Rate Limit (req/s)"
          error={showErrors ? errors.rateLimitPerSec : undefined}
        >
          <Input
            id="webhook-edit-rate-limit"
            type="number"
            min={0}
            value={draft.rateLimitPerSec}
            onChange={(e) =>
              setDraft({
                ...draft,
                rateLimitPerSec: Number(e.currentTarget.value),
              })
            }
            error={showErrors && Boolean(errors.rateLimitPerSec)}
            data-testid="webhook-edit-rate-limit"
          />
        </Field>
        <Field
          id="webhook-edit-concurrency"
          label="동시성"
          error={showErrors ? errors.concurrency : undefined}
        >
          <Input
            id="webhook-edit-concurrency"
            type="number"
            min={1}
            value={draft.concurrency}
            onChange={(e) =>
              setDraft({
                ...draft,
                concurrency: Number(e.currentTarget.value),
              })
            }
            error={showErrors && Boolean(errors.concurrency)}
            data-testid="webhook-edit-concurrency"
          />
        </Field>
      </div>

      <Field
        id="webhook-edit-body-schema"
        label="Body 스키마 (JSON Schema)"
        error={showErrors ? errors.bodySchema : undefined}
      >
        <Textarea
          id="webhook-edit-body-schema"
          rows={6}
          value={draft.bodySchema}
          onChange={(e) =>
            setDraft({ ...draft, bodySchema: e.currentTarget.value })
          }
          className="font-mono text-xs"
          error={showErrors && Boolean(errors.bodySchema)}
          data-testid="webhook-edit-body-schema"
        />
      </Field>
    </Modal>
  );
}

/** 폼 검증 — URL · 시크릿 비빈 / rate / concurrency 양수 / body 빈 문자열 거름. */
export function validate(endpoint: WebhookEndpoint): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!endpoint.url.trim()) {
    errors.url = "URL 을 입력하세요.";
  } else if (!/^https?:\/\//i.test(endpoint.url.trim())) {
    errors.url = "http(s):// 로 시작하는 URL 을 입력하세요.";
  }
  if (!endpoint.secretEnv.trim()) {
    errors.secretEnv = "시크릿 env 변수 명을 입력하세요.";
  }
  if (
    !Number.isFinite(endpoint.rateLimitPerSec) ||
    endpoint.rateLimitPerSec < 0
  ) {
    errors.rateLimitPerSec = "0 이상의 숫자를 입력하세요.";
  }
  if (
    !Number.isFinite(endpoint.concurrency) ||
    endpoint.concurrency < 1 ||
    !Number.isInteger(endpoint.concurrency)
  ) {
    errors.concurrency = "1 이상의 정수를 입력하세요.";
  }
  if (!endpoint.bodySchema.trim()) {
    errors.bodySchema = "Body 스키마를 입력하세요.";
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
