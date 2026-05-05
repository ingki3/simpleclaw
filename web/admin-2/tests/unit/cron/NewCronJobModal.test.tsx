/**
 * NewCronJobModal 단위 테스트 — 검증 / DryRun 미리보기 / submit / cancel.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import {
  NewCronJobModal,
  collectErrors,
} from "@/app/(shell)/cron/_components/NewCronJobModal";
import { parseCron } from "@/app/(shell)/cron/_cron";

describe("NewCronJobModal", () => {
  it("open=false 면 dialog 미렌더", () => {
    render(
      <NewCronJobModal
        open={false}
        onClose={() => {}}
        onSubmit={() => {}}
      />,
    );
    expect(screen.queryByTestId("new-cron-job-modal")).toBeNull();
  });

  it("기본값으로 열리면 DryRun 미리보기가 그려진다", () => {
    render(
      <NewCronJobModal
        open
        onClose={() => {}}
        onSubmit={() => {}}
        now={new Date("2026-05-05T10:00:00")}
      />,
    );
    expect(screen.getByTestId("new-cron-job-modal")).toBeDefined();
    // 기본 표현식 `*/5 * * * *` 미리보기 — 5개 엔트리.
    const items = screen.getAllByTestId("new-cron-job-preview-item");
    expect(items).toHaveLength(5);
  });

  it("이름이 비어 있으면 submit 차단 + 에러 라인 노출", () => {
    const onSubmit = vi.fn();
    render(
      <NewCronJobModal
        open
        onClose={() => {}}
        onSubmit={onSubmit}
        now={new Date("2026-05-05T10:00:00")}
      />,
    );
    fireEvent.click(screen.getByTestId("new-cron-job-submit"));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId("new-cron-job-name-error")).toBeDefined();
  });

  it("Cron 표현식이 잘못되면 submit 차단 + 미리보기 미렌더", () => {
    const onSubmit = vi.fn();
    render(
      <NewCronJobModal
        open
        onClose={() => {}}
        onSubmit={onSubmit}
        now={new Date("2026-05-05T10:00:00")}
      />,
    );
    fireEvent.change(screen.getByTestId("new-cron-job-name"), {
      target: { value: "test-job" },
    });
    fireEvent.change(screen.getByTestId("new-cron-job-schedule"), {
      target: { value: "invalid" },
    });
    expect(screen.queryByTestId("new-cron-job-dry-run")).toBeNull();
    fireEvent.click(screen.getByTestId("new-cron-job-submit"));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId("new-cron-job-schedule-error")).toBeDefined();
  });

  it("정상 입력 시 onSubmit + onClose 호출", () => {
    const onSubmit = vi.fn();
    const onClose = vi.fn();
    render(
      <NewCronJobModal
        open
        onClose={onClose}
        onSubmit={onSubmit}
        now={new Date("2026-05-05T10:00:00")}
      />,
    );
    fireEvent.change(screen.getByTestId("new-cron-job-name"), {
      target: { value: "memory-clean" },
    });
    fireEvent.change(screen.getByTestId("new-cron-job-schedule"), {
      target: { value: "0 3 * * *" },
    });
    fireEvent.click(screen.getByTestId("new-cron-job-submit"));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0]?.[0]).toMatchObject({
      name: "memory-clean",
      schedule: "0 3 * * *",
      scheduleRaw: "0 3 * * *",
      enabled: true,
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("`every 2h` 친화 표기는 normalized=`0 */2 * * *` 로 제출된다", () => {
    const onSubmit = vi.fn();
    render(
      <NewCronJobModal
        open
        onClose={() => {}}
        onSubmit={onSubmit}
        now={new Date("2026-05-05T10:00:00")}
      />,
    );
    fireEvent.change(screen.getByTestId("new-cron-job-name"), {
      target: { value: "cycle" },
    });
    fireEvent.change(screen.getByTestId("new-cron-job-schedule"), {
      target: { value: "every 2h" },
    });
    fireEvent.click(screen.getByTestId("new-cron-job-submit"));
    expect(onSubmit.mock.calls[0]?.[0]).toMatchObject({
      schedule: "0 */2 * * *",
      scheduleRaw: "every 2h",
    });
  });

  it("취소 버튼은 onClose 호출", () => {
    const onClose = vi.fn();
    render(
      <NewCronJobModal
        open
        onClose={onClose}
        onSubmit={() => {}}
        now={new Date("2026-05-05T10:00:00")}
      />,
    );
    fireEvent.click(screen.getByTestId("new-cron-job-cancel"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("collectErrors", () => {
  const validParsed = parseCron("*/5 * * * *");

  it("정상 입력은 빈 객체", () => {
    if (!validParsed.ok) throw new Error("parse should ok");
    const errs = collectErrors({
      name: "memory-clean",
      scheduleRaw: "*/5 * * * *",
      parsed: validParsed,
      payload: "{}",
      timeoutSeconds: 60,
      maxRetries: 1,
    });
    expect(Object.keys(errs)).toHaveLength(0);
  });

  it("이름이 영문으로 시작하지 않으면 실패", () => {
    if (!validParsed.ok) throw new Error("parse should ok");
    const errs = collectErrors({
      name: "1bad",
      scheduleRaw: "*/5 * * * *",
      parsed: validParsed,
      payload: "{}",
      timeoutSeconds: 60,
      maxRetries: 1,
    });
    expect(errs.name).toBeDefined();
  });

  it("payload 가 JSON 객체가 아니면 실패", () => {
    if (!validParsed.ok) throw new Error("parse should ok");
    const errs = collectErrors({
      name: "x",
      scheduleRaw: "*/5 * * * *",
      parsed: validParsed,
      payload: "[1,2,3]",
      timeoutSeconds: 60,
      maxRetries: 1,
    });
    expect(errs.payload).toBeDefined();
  });

  it("payload 가 잘못된 JSON 이면 실패", () => {
    if (!validParsed.ok) throw new Error("parse should ok");
    const errs = collectErrors({
      name: "x",
      scheduleRaw: "*/5 * * * *",
      parsed: validParsed,
      payload: "{not-json",
      timeoutSeconds: 60,
      maxRetries: 1,
    });
    expect(errs.payload).toBeDefined();
  });

  it("timeoutSeconds 가 0 이하면 실패", () => {
    if (!validParsed.ok) throw new Error("parse should ok");
    const errs = collectErrors({
      name: "x",
      scheduleRaw: "*/5 * * * *",
      parsed: validParsed,
      payload: "{}",
      timeoutSeconds: 0,
      maxRetries: 1,
    });
    expect(errs.timeoutSeconds).toBeDefined();
  });

  it("maxRetries 가 음수면 실패 (0 은 허용)", () => {
    if (!validParsed.ok) throw new Error("parse should ok");
    const ok = collectErrors({
      name: "x",
      scheduleRaw: "*/5 * * * *",
      parsed: validParsed,
      payload: "{}",
      timeoutSeconds: 60,
      maxRetries: 0,
    });
    expect(ok.maxRetries).toBeUndefined();
    const bad = collectErrors({
      name: "x",
      scheduleRaw: "*/5 * * * *",
      parsed: validParsed,
      payload: "{}",
      timeoutSeconds: 60,
      maxRetries: -1,
    });
    expect(bad.maxRetries).toBeDefined();
  });
});
