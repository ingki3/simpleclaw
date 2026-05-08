/**
 * EditCronJobModal 단위 테스트 — prefill / 저장 / 취소 / 입력 검증.
 *
 * BIZ-157 DoD §5 — 4 시나리오 박제. Create modal 의 검증 로직 (`collectErrors`)
 * 을 그대로 재사용하므로 본 테스트는 prefill 동작과 onSave 콜백 shape 에 집중.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { EditCronJobModal } from "@/app/(shell)/cron/_components/EditCronJobModal";
import { JOB_ENABLED } from "./_fixture";

describe("EditCronJobModal", () => {
  it("prefill — job 의 schedule/payload/timeout 등을 초기값으로 채운다", () => {
    render(
      <EditCronJobModal
        open
        job={JOB_ENABLED}
        onClose={() => {}}
        onSave={() => {}}
        now={new Date("2026-05-05T10:00:00")}
      />,
    );
    // 이름은 read-only 로 prefill — 변경 불가.
    const name = screen.getByTestId("edit-cron-job-name") as HTMLInputElement;
    expect(name.value).toBe(JOB_ENABLED.name);
    expect(name.readOnly).toBe(true);
    // 스케줄/페이로드/타임아웃/재시도 — 모두 prefill.
    expect(
      (screen.getByTestId("edit-cron-job-schedule") as HTMLInputElement).value,
    ).toBe(JOB_ENABLED.schedule);
    expect(
      (screen.getByTestId("edit-cron-job-payload") as HTMLTextAreaElement).value,
    ).toBe(JOB_ENABLED.payload);
    expect(
      (screen.getByTestId("edit-cron-job-timeout") as HTMLInputElement).value,
    ).toBe(String(JOB_ENABLED.timeoutSeconds));
    expect(
      (screen.getByTestId("edit-cron-job-max-retries") as HTMLInputElement).value,
    ).toBe(String(JOB_ENABLED.maxRetries));
    // DryRun 미리보기 — 5개 엔트리 (변경 후 슬롯).
    expect(
      screen.getAllByTestId("edit-cron-job-preview-item"),
    ).toHaveLength(5);
  });

  it("저장 — 변경된 schedule 로 onSave(id, input) + onClose 호출", () => {
    const onSave = vi.fn();
    const onClose = vi.fn();
    render(
      <EditCronJobModal
        open
        job={JOB_ENABLED}
        onClose={onClose}
        onSave={onSave}
        now={new Date("2026-05-05T10:00:00")}
      />,
    );
    fireEvent.change(screen.getByTestId("edit-cron-job-schedule"), {
      target: { value: "0 4 * * *" },
    });
    fireEvent.click(screen.getByTestId("edit-cron-job-submit"));
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0]?.[0]).toBe(JOB_ENABLED.id);
    expect(onSave.mock.calls[0]?.[1]).toMatchObject({
      name: JOB_ENABLED.name,
      schedule: "0 4 * * *",
      scheduleRaw: "0 4 * * *",
      enabled: JOB_ENABLED.enabled,
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("취소 — onClose 만 호출되고 onSave 는 호출되지 않는다", () => {
    const onSave = vi.fn();
    const onClose = vi.fn();
    render(
      <EditCronJobModal
        open
        job={JOB_ENABLED}
        onClose={onClose}
        onSave={onSave}
        now={new Date("2026-05-05T10:00:00")}
      />,
    );
    fireEvent.click(screen.getByTestId("edit-cron-job-cancel"));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onSave).not.toHaveBeenCalled();
  });

  it("입력 검증 — schedule 가 잘못되면 onSave 차단 + 에러 라인 노출", () => {
    const onSave = vi.fn();
    render(
      <EditCronJobModal
        open
        job={JOB_ENABLED}
        onClose={() => {}}
        onSave={onSave}
        now={new Date("2026-05-05T10:00:00")}
      />,
    );
    fireEvent.change(screen.getByTestId("edit-cron-job-schedule"), {
      target: { value: "invalid-cron" },
    });
    // 미리보기는 사라지고 (parsed.ok=false), 제출 시도해도 onSave 미호출.
    expect(screen.queryByTestId("edit-cron-job-dry-run")).toBeNull();
    fireEvent.click(screen.getByTestId("edit-cron-job-submit"));
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByTestId("edit-cron-job-schedule-error")).toBeDefined();
  });
});
