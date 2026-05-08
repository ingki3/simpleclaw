/**
 * CronJobsList 단위 테스트 — 4-variant + 토글/실행 콜백.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { CronJobsList } from "@/app/(shell)/cron/_components/CronJobsList";
import { JOB_ENABLED, JOB_LIST } from "./_fixture";

describe("CronJobsList", () => {
  it("default variant — 테이블에 잡이 모두 렌더된다", () => {
    render(
      <CronJobsList
        state="default"
        jobs={JOB_LIST}
        onToggleEnabled={() => {}}
      />,
    );
    expect(screen.getByTestId("cron-jobs-table")).toBeDefined();
    expect(screen.getByTestId(`cron-job-${JOB_ENABLED.id}-toggle`)).toBeDefined();
  });

  it("loading variant — aria-busy=true + 스켈레톤", () => {
    render(<CronJobsList state="loading" onToggleEnabled={() => {}} />);
    const list = screen.getByTestId("cron-jobs-list");
    expect(list.getAttribute("aria-busy")).toBe("true");
    expect(screen.getByTestId("cron-jobs-list-loading")).toBeDefined();
  });

  it("error variant — alert role 노출 + retry 콜백", () => {
    const onRetry = vi.fn();
    render(
      <CronJobsList
        state="error"
        onToggleEnabled={() => {}}
        onRetry={onRetry}
      />,
    );
    const err = screen.getByTestId("cron-jobs-list-error");
    expect(err.getAttribute("role")).toBe("alert");
    fireEvent.click(screen.getByTestId("cron-jobs-list-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("empty variant — `+ 새 작업` CTA 노출", () => {
    const onCreate = vi.fn();
    render(
      <CronJobsList
        state="empty"
        onToggleEnabled={() => {}}
        onCreate={onCreate}
      />,
    );
    expect(screen.getByTestId("cron-jobs-list-empty")).toBeDefined();
    fireEvent.click(screen.getByText("＋ 새 작업"));
    expect(onCreate).toHaveBeenCalledTimes(1);
  });

  it("default variant + jobs=[] + searchQuery — filtered empty 메시지", () => {
    render(
      <CronJobsList
        state="default"
        jobs={[]}
        searchQuery="없는키워드"
        onToggleEnabled={() => {}}
        onCreate={() => {}}
      />,
    );
    const empty = screen.getByTestId("cron-jobs-list-empty");
    expect(empty.getAttribute("data-empty-reason")).toBe("filtered");
  });

  it("Switch 클릭 시 onToggleEnabled(id, next) 호출", () => {
    const onToggle = vi.fn();
    render(
      <CronJobsList
        state="default"
        jobs={JOB_LIST}
        onToggleEnabled={onToggle}
      />,
    );
    fireEvent.click(
      screen.getByTestId(`cron-job-${JOB_ENABLED.id}-toggle`),
    );
    expect(onToggle).toHaveBeenCalledWith(JOB_ENABLED.id, !JOB_ENABLED.enabled);
  });

  it("실행 버튼 클릭 시 onRunNow(id) 호출", () => {
    const onRunNow = vi.fn();
    render(
      <CronJobsList
        state="default"
        jobs={JOB_LIST}
        onToggleEnabled={() => {}}
        onRunNow={onRunNow}
      />,
    );
    fireEvent.click(screen.getByTestId(`cron-job-${JOB_ENABLED.id}-run`));
    expect(onRunNow).toHaveBeenCalledWith(JOB_ENABLED.id);
  });
});
