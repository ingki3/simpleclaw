"use client";

/**
 * EditCronJobModal — admin.pen `Y0X0SZ` Edit variant frame (BIZ-156) 박제.
 *
 * `NewCronJobModal` 의 prefill 변형 — 기존 `CronJob` 을 받아 폼을 채우고
 * `onSave(id, NewCronJobInput)` 으로 mutation 만 위임한다. 시각 spec 은
 * Create modal 과 동일한 토큰 / 카드 레이아웃을 재사용한다 (DESIGN.md §1
 * Principle 2 — 한 spec 한 컴포넌트).
 *
 * 폼 필드 (위 → 아래):
 *  - 작업 이름 (read-only — id 매칭이 PATCH endpoint 의 path 키이므로 잠금)
 *  - Cron 표현식 (편집 가능, 친화 표기 허용)
 *  - 대상 스킬 (Select)
 *  - Payload (JSON textarea)
 *  - Timeout / Max Retries (한 행)
 *  - 활성화 Switch
 *
 * DryRunCard 는 `before` = 기존 스케줄 다음 실행, `after` = 새 스케줄 다음 실행으로
 * 변경 영향을 시각적으로 비교한다. 검증 실패 시 "저장" 버튼 disabled.
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
import type { CronJob } from "../_data";
import { Modal } from "./Modal";
import { collectErrors, type NewCronJobInput } from "./NewCronJobModal";

interface EditCronJobModalProps {
  open: boolean;
  /** 편집 대상 — 닫혀 있을 때는 null 허용 (페이지가 editingJobId 로 lookup 후 전달). */
  job: CronJob | null;
  /** 대상 스킬 후보 — Create modal 과 동일 spec. */
  skillOptions?: readonly SelectOption[];
  onClose: () => void;
  /** 검증 통과 후 호출. id 는 path 매칭용이고, input 은 PATCH body 와 동일 shape. */
  onSave: (id: string, input: NewCronJobInput) => void;
  /** 미리보기 기준 시각 — 테스트에서 결정성 확보용. */
  now?: Date;
}

const PREVIEW_COUNT = 5;
const NO_SKILL_VALUE = "__none__";

export function EditCronJobModal({
  open,
  job,
  skillOptions = [],
  onClose,
  onSave,
  now,
}: EditCronJobModalProps) {
  const [scheduleRaw, setScheduleRaw] = useState("");
  const [skillId, setSkillId] = useState<string>(NO_SKILL_VALUE);
  const [payload, setPayload] = useState("{}");
  const [timeoutSeconds, setTimeoutSeconds] = useState(60);
  const [maxRetries, setMaxRetries] = useState(0);
  const [enabled, setEnabled] = useState(true);
  const [submitted, setSubmitted] = useState(false);

  // job 또는 open 이 바뀔 때 prefill — 이전 편집 흔적이 남지 않도록 baseline 갱신.
  useEffect(() => {
    if (!open || !job) return;
    setScheduleRaw(job.schedule);
    setSkillId(job.skillId ?? NO_SKILL_VALUE);
    setPayload(job.payload);
    setTimeoutSeconds(job.timeoutSeconds);
    setMaxRetries(job.maxRetries);
    setEnabled(job.enabled);
    setSubmitted(false);
  }, [open, job]);

  // 입력이 안정될 때만 다시 파싱.
  const parsed: CronParseResult = useMemo(
    () => parseCron(scheduleRaw),
    [scheduleRaw],
  );

  // open 이 토글되어 다시 열릴 때마다 baseline 을 갱신해 미리보기가 stale 해지지 않도록.
  const previewBaseline = useMemo(() => now ?? new Date(), [now, open]);
  const previewRuns = useMemo(
    () => (parsed.ok ? nextRuns(parsed, previewBaseline, PREVIEW_COUNT) : []),
    [parsed, previewBaseline],
  );

  // 기존 스케줄의 다음 실행 — `before` 슬롯에 노출하기 위해 동일 엔진으로 계산.
  const beforeParsed: CronParseResult = useMemo(
    () => (job ? parseCron(job.schedule) : { ok: false, message: "" }),
    [job],
  );
  const beforeRuns = useMemo(
    () =>
      beforeParsed.ok ? nextRuns(beforeParsed, previewBaseline, PREVIEW_COUNT) : [],
    [beforeParsed, previewBaseline],
  );

  // job 이 없으면 폼을 그릴 수 없으므로 검증을 건너뛴다 (open=false 와 동일 처리).
  const errors = job
    ? collectErrors({
        name: job.name,
        scheduleRaw,
        parsed,
        payload,
        timeoutSeconds,
        maxRetries,
      })
    : {};
  const valid = Object.keys(errors).length === 0;
  const showErrors = submitted;

  const skillSelectOptions: SelectOption[] = [
    { value: NO_SKILL_VALUE, label: "선택 안 함 (페이로드만 발화)" },
    ...skillOptions,
  ];

  if (!open || !job) {
    return (
      <Modal open={false} onClose={onClose} title={null} footer={null}>
        {null}
      </Modal>
    );
  }

  const handleSave = () => {
    setSubmitted(true);
    if (!valid || !parsed.ok) return;
    onSave(job.id, {
      name: job.name,
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
      data-testid="edit-cron-job-modal"
      title={
        <div className="flex flex-col gap-0.5">
          <h2 className="text-lg font-semibold text-(--foreground-strong)">
            잡 수정
          </h2>
          <p className="text-xs text-(--muted-foreground)">
            <Code>{job.name}</Code> 의 스케줄·페이로드·재시도 정책을 변경합니다.
          </p>
        </div>
      }
      footer={
        <>
          <Button
            variant="ghost"
            onClick={onClose}
            data-testid="edit-cron-job-cancel"
          >
            취소
          </Button>
          <Button
            variant="primary"
            onClick={handleSave}
            disabled={showErrors && !valid}
            data-testid="edit-cron-job-submit"
          >
            저장
          </Button>
        </>
      }
    >
      <Field id="edit-cron-job-name" label="작업 이름">
        <Input
          id="edit-cron-job-name"
          value={job.name}
          readOnly
          aria-readonly="true"
          data-testid="edit-cron-job-name"
        />
        <p className="text-xs text-(--muted-foreground)">
          이름은 PATCH endpoint 의 path 키로 잠겨 있습니다 — 변경하려면 잡을
          삭제 후 재생성하세요.
        </p>
      </Field>

      <Field
        id="edit-cron-job-schedule"
        label="Cron 표현식"
        error={showErrors ? errors.schedule : undefined}
      >
        <Input
          id="edit-cron-job-schedule"
          value={scheduleRaw}
          autoFocus
          onChange={(e) => setScheduleRaw(e.currentTarget.value)}
          placeholder="*/5 * * * *  또는  every 2h"
          error={showErrors && Boolean(errors.schedule)}
          className="font-mono"
          data-testid="edit-cron-job-schedule"
        />
        <p className="text-xs text-(--muted-foreground)">
          예: <Code>*/5 * * * *</Code> (5분마다 실행), <Code>0 9 * * MON</Code>{" "}
          (월요일 9시), <Code>every 2h</Code>.
        </p>
        {!showErrors && !parsed.ok && scheduleRaw.trim().length > 0 ? (
          <p
            className="text-xs text-(--muted-foreground)"
            data-testid="edit-cron-job-schedule-hint"
          >
            {parsed.message}
          </p>
        ) : null}
      </Field>

      <Field id="edit-cron-job-skill" label="대상 스킬">
        <Select
          id="edit-cron-job-skill"
          options={skillSelectOptions}
          value={skillId}
          onChange={(e) => setSkillId(e.currentTarget.value)}
          data-testid="edit-cron-job-skill"
        />
      </Field>

      <Field
        id="edit-cron-job-payload"
        label="Payload (JSON)"
        error={showErrors ? errors.payload : undefined}
      >
        <Textarea
          id="edit-cron-job-payload"
          value={payload}
          onChange={(e) => setPayload(e.currentTarget.value)}
          rows={5}
          className="font-mono"
          error={showErrors && Boolean(errors.payload)}
          data-testid="edit-cron-job-payload"
        />
        <p className="text-xs text-(--muted-foreground)">
          유효한 JSON 객체 — 비어 있을 수 있습니다 (<Code>{"{}"}</Code>).
        </p>
      </Field>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field
          id="edit-cron-job-timeout"
          label="Timeout (초)"
          error={showErrors ? errors.timeoutSeconds : undefined}
        >
          <Input
            id="edit-cron-job-timeout"
            type="number"
            min={1}
            value={timeoutSeconds}
            onChange={(e) =>
              setTimeoutSeconds(Number(e.currentTarget.value))
            }
            error={showErrors && Boolean(errors.timeoutSeconds)}
            data-testid="edit-cron-job-timeout"
          />
        </Field>
        <Field
          id="edit-cron-job-max-retries"
          label="Max Retries"
          error={showErrors ? errors.maxRetries : undefined}
        >
          <Input
            id="edit-cron-job-max-retries"
            type="number"
            min={0}
            value={maxRetries}
            onChange={(e) => setMaxRetries(Number(e.currentTarget.value))}
            error={showErrors && Boolean(errors.maxRetries)}
            data-testid="edit-cron-job-max-retries"
          />
        </Field>
      </div>

      <div className="flex items-center justify-between gap-3 rounded-(--radius-m) border border-(--border) bg-(--surface) px-4 py-3">
        <div className="flex flex-col">
          <span className="text-sm font-medium text-(--foreground)">
            활성화
          </span>
          <span className="text-xs text-(--muted-foreground)">
            저장 즉시 데몬에 반영됩니다.
          </span>
        </div>
        <Switch
          checked={enabled}
          onCheckedChange={setEnabled}
          label="활성화"
          data-testid="edit-cron-job-enabled"
        />
      </div>

      {parsed.ok ? (
        <div data-testid="edit-cron-job-dry-run">
          <DryRunCard
            before={
              <div
                className="flex flex-col gap-1.5"
                data-testid="edit-cron-job-before"
              >
                <span className="font-mono text-xs text-(--foreground)">
                  {beforeParsed.ok ? beforeParsed.normalized : job.schedule}
                </span>
                <span className="text-xs text-(--muted-foreground)">
                  {beforeParsed.ok
                    ? beforeParsed.description
                    : "기존 스케줄을 해석할 수 없습니다."}
                </span>
                <ul
                  className="mt-1 flex flex-col gap-1"
                  aria-label="기존 다음 5회 실행 시각"
                >
                  {beforeRuns.length === 0 ? (
                    <li className="text-xs text-(--muted-foreground)">
                      예측 가능한 다음 실행이 없습니다.
                    </li>
                  ) : (
                    beforeRuns.map((d) => (
                      <li
                        key={d.toISOString()}
                        className="font-mono text-xs text-(--muted-foreground)"
                      >
                        {formatPreview(d)}
                      </li>
                    ))
                  )}
                </ul>
              </div>
            }
            after={
              <div
                className="flex flex-col gap-1.5"
                data-testid="edit-cron-job-after"
              >
                <span className="font-mono text-xs text-(--foreground)">
                  {parsed.normalized}
                </span>
                <span className="text-xs text-(--muted-foreground)">
                  {parsed.description}
                </span>
                <ul
                  className="mt-1 flex flex-col gap-1"
                  aria-label="변경 후 다음 5회 실행 시각"
                >
                  {previewRuns.length === 0 ? (
                    <li className="text-xs text-(--muted-foreground)">
                      예측 가능한 다음 실행이 없습니다.
                    </li>
                  ) : (
                    previewRuns.map((d) => (
                      <li
                        key={d.toISOString()}
                        data-testid="edit-cron-job-preview-item"
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

/** 미리보기 행의 사람 친화 시각 표기 — 모달 한 줄. */
function formatPreview(d: Date): string {
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd} ${hh}:${mi}`;
}
