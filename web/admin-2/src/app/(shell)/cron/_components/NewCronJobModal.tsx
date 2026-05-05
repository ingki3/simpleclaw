"use client";

/**
 * NewCronJobModal — admin.pen `Y0X0SZ` (New Cron Job modal) 박제.
 *
 * 폼 필드 (위 → 아래):
 *  - 작업 이름 (예: daily-memory-cleanup)
 *  - Cron 표현식 (예: `*\/5 * * * *`)
 *  - 대상 스킬 (Select — 설치된 스킬 + "선택 안 함")
 *  - Payload (JSON textarea)
 *  - Timeout (초) / Max Retries (한 행)
 *  - 활성화 Switch
 *
 * Cron 표현식이 유효하면 본문 하단에 DryRunCard 미리보기 (다음 5회 실행 시각).
 * 검증 실패 시 "생성" 버튼 disabled.
 *
 * skills-recipes 의 RetryPolicyModal 패턴 그대로 — `submitted` 상태로 첫 클릭
 * 이전에는 에러 라인 미노출.
 */

import { useEffect, useMemo, useState } from "react";
import { Button } from "@/design/atoms/Button";
import { Input } from "@/design/atoms/Input";
import { Label } from "@/design/atoms/Label";
import { Select, type SelectOption } from "@/design/atoms/Select";
import { Switch } from "@/design/atoms/Switch";
import { Textarea } from "@/design/atoms/Textarea";
import { Code } from "@/design/atoms/Code";
import { DryRunCard } from "@/design/molecules/DryRunCard";
import { nextRuns, parseCron, type CronParseResult } from "../_cron";
import { Modal } from "./Modal";

/** 새 잡 입력 — 부모(page) 에 그대로 전달되어 fixture 에 추가된다. */
export interface NewCronJobInput {
  name: string;
  /** 정규화된 5-필드 표현식 — `expandFriendly` 통과본. */
  schedule: string;
  /** 사용자가 입력한 원본 표현식 — 친화 표기 보존을 위한 보조 필드. */
  scheduleRaw: string;
  skillId?: string;
  payload: string;
  timeoutSeconds: number;
  maxRetries: number;
  enabled: boolean;
}

interface NewCronJobModalProps {
  open: boolean;
  /** 대상 스킬 후보 목록. 빈 배열이면 Select 가 "선택 안 함" 만 노출. */
  skillOptions?: readonly SelectOption[];
  onClose: () => void;
  /** 검증 통과 후 호출. 부모가 fixture/state 갱신을 담당. */
  onSubmit: (input: NewCronJobInput) => void;
  /** 미리보기 기준 시각 — 테스트에서 결정성 확보용. 기본값은 현재. */
  now?: Date;
}

const PREVIEW_COUNT = 5;
const NO_SKILL_VALUE = "__none__";

export function NewCronJobModal({
  open,
  skillOptions = [],
  onClose,
  onSubmit,
  now,
}: NewCronJobModalProps) {
  const [name, setName] = useState("");
  const [scheduleRaw, setScheduleRaw] = useState("*/5 * * * *");
  const [skillId, setSkillId] = useState<string>(NO_SKILL_VALUE);
  const [payload, setPayload] = useState('{\n  "target": "memory",\n  "mode": "compact"\n}');
  const [timeoutSeconds, setTimeoutSeconds] = useState(300);
  const [maxRetries, setMaxRetries] = useState(3);
  const [enabled, setEnabled] = useState(true);
  const [submitted, setSubmitted] = useState(false);

  // open 이 토글되어 닫혔다 다시 열릴 때만 초기화 — race 보호.
  useEffect(() => {
    if (open) {
      setName("");
      setScheduleRaw("*/5 * * * *");
      setSkillId(NO_SKILL_VALUE);
      setPayload('{\n  "target": "memory",\n  "mode": "compact"\n}');
      setTimeoutSeconds(300);
      setMaxRetries(3);
      setEnabled(true);
      setSubmitted(false);
    }
  }, [open]);

  // useMemo — 입력이 안정될 때만 다시 파싱.
  const parsed: CronParseResult = useMemo(
    () => parseCron(scheduleRaw),
    [scheduleRaw],
  );

  // `open` 이 토글되어 다시 열릴 때마다 baseline 을 갱신해, 미리보기가 stale 해지지 않도록.
  const previewBaseline = useMemo(() => now ?? new Date(), [now, open]);
  const previewRuns = useMemo(
    () => (parsed.ok ? nextRuns(parsed, previewBaseline, PREVIEW_COUNT) : []),
    [parsed, previewBaseline],
  );

  const errors = collectErrors({
    name,
    scheduleRaw,
    parsed,
    payload,
    timeoutSeconds,
    maxRetries,
  });
  const valid = Object.keys(errors).length === 0;
  const showErrors = submitted;

  const skillSelectOptions: SelectOption[] = [
    { value: NO_SKILL_VALUE, label: "선택 안 함 (페이로드만 발화)" },
    ...skillOptions,
  ];

  if (!open) {
    return (
      <Modal open={false} onClose={onClose} title={null} footer={null}>
        {null}
      </Modal>
    );
  }

  const handleSubmit = () => {
    setSubmitted(true);
    if (!valid || !parsed.ok) return;
    onSubmit({
      name: name.trim(),
      schedule: parsed.normalized,
      scheduleRaw: scheduleRaw.trim(),
      skillId: skillId === NO_SKILL_VALUE ? undefined : skillId,
      payload,
      timeoutSeconds,
      maxRetries,
      enabled,
    });
    onClose();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      width="lg"
      data-testid="new-cron-job-modal"
      title={
        <div className="flex flex-col gap-0.5">
          <h2 className="text-lg font-semibold text-(--foreground-strong)">
            새 Cron 작업
          </h2>
          <p className="text-xs text-(--muted-foreground)">
            crontab 표현식 또는 사람 친화 표기 (`every 2h`) 를 입력하세요.
          </p>
        </div>
      }
      footer={
        <>
          <Button
            variant="ghost"
            onClick={onClose}
            data-testid="new-cron-job-cancel"
          >
            취소
          </Button>
          <Button
            variant="primary"
            onClick={handleSubmit}
            disabled={showErrors && !valid}
            data-testid="new-cron-job-submit"
          >
            생성
          </Button>
        </>
      }
    >
      <Field
        id="new-cron-job-name"
        label="작업 이름"
        error={showErrors ? errors.name : undefined}
      >
        <Input
          id="new-cron-job-name"
          value={name}
          autoFocus
          onChange={(e) => setName(e.currentTarget.value)}
          placeholder="예: daily-memory-cleanup"
          error={showErrors && Boolean(errors.name)}
          data-testid="new-cron-job-name"
        />
      </Field>

      <Field
        id="new-cron-job-schedule"
        label="Cron 표현식"
        error={showErrors ? errors.schedule : undefined}
      >
        <Input
          id="new-cron-job-schedule"
          value={scheduleRaw}
          onChange={(e) => setScheduleRaw(e.currentTarget.value)}
          placeholder="*/5 * * * *  또는  every 2h"
          error={showErrors && Boolean(errors.schedule)}
          className="font-mono"
          data-testid="new-cron-job-schedule"
        />
        <p className="text-xs text-(--muted-foreground)">
          예: <Code>*/5 * * * *</Code> (5분마다 실행), <Code>0 9 * * MON</Code>{" "}
          (월요일 9시), <Code>every 2h</Code>.
        </p>
        {!showErrors && !parsed.ok && scheduleRaw.trim().length > 0 ? (
          <p
            className="text-xs text-(--muted-foreground)"
            data-testid="new-cron-job-schedule-hint"
          >
            {parsed.message}
          </p>
        ) : null}
      </Field>

      <Field id="new-cron-job-skill" label="대상 스킬">
        <Select
          id="new-cron-job-skill"
          options={skillSelectOptions}
          value={skillId}
          onChange={(e) => setSkillId(e.currentTarget.value)}
          data-testid="new-cron-job-skill"
        />
      </Field>

      <Field
        id="new-cron-job-payload"
        label="Payload (JSON)"
        error={showErrors ? errors.payload : undefined}
      >
        <Textarea
          id="new-cron-job-payload"
          value={payload}
          onChange={(e) => setPayload(e.currentTarget.value)}
          rows={5}
          className="font-mono"
          error={showErrors && Boolean(errors.payload)}
          data-testid="new-cron-job-payload"
        />
        <p className="text-xs text-(--muted-foreground)">
          유효한 JSON 객체 — 비어 있을 수 있습니다 (<Code>{"{}"}</Code>).
        </p>
      </Field>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field
          id="new-cron-job-timeout"
          label="Timeout (초)"
          error={showErrors ? errors.timeoutSeconds : undefined}
        >
          <Input
            id="new-cron-job-timeout"
            type="number"
            min={1}
            value={timeoutSeconds}
            onChange={(e) =>
              setTimeoutSeconds(Number(e.currentTarget.value))
            }
            error={showErrors && Boolean(errors.timeoutSeconds)}
            data-testid="new-cron-job-timeout"
          />
        </Field>
        <Field
          id="new-cron-job-max-retries"
          label="Max Retries"
          error={showErrors ? errors.maxRetries : undefined}
        >
          <Input
            id="new-cron-job-max-retries"
            type="number"
            min={0}
            value={maxRetries}
            onChange={(e) => setMaxRetries(Number(e.currentTarget.value))}
            error={showErrors && Boolean(errors.maxRetries)}
            data-testid="new-cron-job-max-retries"
          />
        </Field>
      </div>

      <div className="flex items-center justify-between gap-3 rounded-(--radius-m) border border-(--border) bg-(--surface) px-4 py-3">
        <div className="flex flex-col">
          <span className="text-sm font-medium text-(--foreground)">
            활성화
          </span>
          <span className="text-xs text-(--muted-foreground)">
            생성 즉시 스케줄러에 등록됩니다.
          </span>
        </div>
        <Switch
          checked={enabled}
          onCheckedChange={setEnabled}
          label="활성화"
          data-testid="new-cron-job-enabled"
        />
      </div>

      {parsed.ok ? (
        <div data-testid="new-cron-job-dry-run">
        <DryRunCard
          before={
            <span className="text-(--muted-foreground)">없음 — 새 잡</span>
          }
          after={
            <div
              className="flex flex-col gap-1.5"
              data-testid="new-cron-job-preview"
            >
              <span className="font-mono text-xs text-(--foreground)">
                {parsed.normalized}
              </span>
              <span className="text-xs text-(--muted-foreground)">
                {parsed.description}
              </span>
              <ul
                className="mt-1 flex flex-col gap-1"
                aria-label="다음 5회 실행 시각"
              >
                {previewRuns.length === 0 ? (
                  <li className="text-xs text-(--muted-foreground)">
                    예측 가능한 다음 실행이 없습니다.
                  </li>
                ) : (
                  previewRuns.map((d) => (
                    <li
                      key={d.toISOString()}
                      data-testid="new-cron-job-preview-item"
                      className="font-mono text-xs text-(--foreground)"
                    >
                      {formatPreview(d)}
                    </li>
                  ))
                )}
              </ul>
            </div>
          }
          impact={`${PREVIEW_COUNT}회 미리보기 — 데몬이 발화 시각을 최종 확정합니다.`}
        />
        </div>
      ) : null}
    </Modal>
  );
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

interface CollectErrorsArgs {
  name: string;
  scheduleRaw: string;
  parsed: CronParseResult;
  payload: string;
  timeoutSeconds: number;
  maxRetries: number;
}

/** 폼 검증 — 빈 이름·잘못된 표현식·잘못된 JSON·비양수 timeout/retries 를 거른다. */
export function collectErrors({
  name,
  scheduleRaw,
  parsed,
  payload,
  timeoutSeconds,
  maxRetries,
}: CollectErrorsArgs): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!name.trim()) {
    errors.name = "이름은 비어 있을 수 없습니다.";
  } else if (!/^[a-zA-Z][\w.-]*$/.test(name.trim())) {
    errors.name =
      "이름은 영문으로 시작하고 영문/숫자/`.`/`_`/`-` 만 허용합니다.";
  }
  if (!scheduleRaw.trim()) {
    errors.schedule = "Cron 표현식을 입력하세요.";
  } else if (!parsed.ok) {
    errors.schedule = parsed.message;
  }
  const payloadTrimmed = payload.trim();
  if (payloadTrimmed.length === 0) {
    errors.payload = "JSON 객체를 입력하세요 (`{}` 도 가능).";
  } else {
    try {
      const parsedPayload = JSON.parse(payloadTrimmed);
      if (
        typeof parsedPayload !== "object" ||
        parsedPayload === null ||
        Array.isArray(parsedPayload)
      ) {
        errors.payload = "JSON 객체만 허용합니다 (배열/원시값 금지).";
      }
    } catch {
      errors.payload = "유효한 JSON 이 아닙니다.";
    }
  }
  if (
    !Number.isFinite(timeoutSeconds) ||
    timeoutSeconds < 1 ||
    !Number.isInteger(timeoutSeconds)
  ) {
    errors.timeoutSeconds = "1 이상의 정수(초) 를 입력하세요.";
  }
  if (
    !Number.isFinite(maxRetries) ||
    maxRetries < 0 ||
    !Number.isInteger(maxRetries)
  ) {
    errors.maxRetries = "0 이상의 정수를 입력하세요.";
  }
  return errors;
}

/** 미리보기 행의 사람 친화 시각 표기 — 모달 한 줄. */
function formatPreview(d: Date): string {
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd} ${hh}:${mi}`;
}
