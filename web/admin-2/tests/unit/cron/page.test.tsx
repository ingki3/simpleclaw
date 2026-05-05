/**
 * /cron 페이지 통합 단위 테스트 — 헤더 + 4-variant + 모달/토글/검색.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

const mockSearchParams = vi.fn(() => new URLSearchParams());

vi.mock("next/navigation", () => ({
  useSearchParams: () => mockSearchParams(),
  usePathname: () => "/cron",
}));

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children: React.ReactNode;
  } & Record<string, unknown>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import CronPage from "@/app/(shell)/cron/page";

describe("CronPage", () => {
  it("h1 + 잡 목록 + 히스토리 카드 섹션을 렌더한다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<CronPage />);

    expect(
      screen.getByRole("heading", { level: 1, name: "크론" }),
    ).toBeDefined();
    expect(screen.getByTestId("cron-jobs-list")).toBeDefined();
    expect(screen.getByTestId("cron-history")).toBeDefined();
    expect(screen.getByTestId("cron-search")).toBeDefined();
  });

  it("?jobs=loading 면 잡 목록이 loading variant", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams("jobs=loading"));
    render(<CronPage />);
    expect(
      screen.getByTestId("cron-jobs-list").getAttribute("data-state"),
    ).toBe("loading");
    expect(screen.getByTestId("cron-jobs-list-loading")).toBeDefined();
  });

  it("?jobs=empty 면 잡 목록이 empty variant", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams("jobs=empty"));
    render(<CronPage />);
    expect(
      screen.getByTestId("cron-jobs-list").getAttribute("data-state"),
    ).toBe("empty");
    expect(screen.getByTestId("cron-jobs-list-empty")).toBeDefined();
  });

  it("?jobs=error 면 잡 목록이 error variant", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams("jobs=error"));
    render(<CronPage />);
    expect(
      screen.getByTestId("cron-jobs-list").getAttribute("data-state"),
    ).toBe("error");
    expect(screen.getByTestId("cron-jobs-list-error")).toBeDefined();
  });

  it("`+ 새 작업` 버튼 클릭 시 NewCronJobModal 이 열린다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<CronPage />);
    fireEvent.click(screen.getByTestId("cron-create"));
    expect(screen.getByTestId("new-cron-job-modal")).toBeDefined();
  });

  it("Switch 토글 시 fixture 가 즉시 갱신된다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<CronPage />);
    const toggle = screen.getByTestId("cron-job-dreaming-cycle-toggle");
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(toggle);
    expect(
      screen.getByTestId("cron-job-dreaming-cycle-toggle").getAttribute(
        "aria-checked",
      ),
    ).toBe("false");
  });

  it("검색 입력에 키워드를 넣으면 잡이 필터링된다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<CronPage />);
    fireEvent.change(screen.getByTestId("cron-search"), {
      target: { value: "memory" },
    });
    expect(screen.getByTestId("cron-job-memory-compact-toggle")).toBeDefined();
    expect(screen.queryByTestId("cron-job-dreaming-cycle-toggle")).toBeNull();
  });

  it("새 잡 생성 시 목록에 즉시 추가된다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<CronPage />);
    fireEvent.click(screen.getByTestId("cron-create"));
    fireEvent.change(screen.getByTestId("new-cron-job-name"), {
      target: { value: "fresh-job" },
    });
    fireEvent.change(screen.getByTestId("new-cron-job-schedule"), {
      target: { value: "0 4 * * *" },
    });
    fireEvent.click(screen.getByTestId("new-cron-job-submit"));
    // 모달이 닫히고 fixture 에 새 잡 카드가 등장.
    expect(screen.queryByTestId("new-cron-job-modal")).toBeNull();
    expect(screen.getByTestId("cron-job-fresh-job-toggle")).toBeDefined();
  });

  it("페이지 헤더 카운트 — 활성/일시정지/circuit-open 노출", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<CronPage />);
    const counts = screen.getByTestId("cron-counts");
    expect(within(counts).getByText(/실행/)).toBeDefined();
    expect(within(counts).getByText(/일시정지/)).toBeDefined();
    expect(within(counts).getByText(/circuit-open/)).toBeDefined();
  });
});
