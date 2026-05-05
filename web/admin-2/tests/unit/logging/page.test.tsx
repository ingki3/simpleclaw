/**
 * /logging 페이지 통합 단위 테스트 — 9 cases.
 *
 * 헤더 + 4-variant + 4개 필터 + 행 클릭 → 모달.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

const mockSearchParams = vi.fn(() => new URLSearchParams());

vi.mock("next/navigation", () => ({
  useSearchParams: () => mockSearchParams(),
  usePathname: () => "/logging",
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

import LoggingPage from "@/app/(shell)/logging/page";

describe("LoggingPage", () => {
  it("h1 + 4개 필터 + 표 + 카운트 섹션을 렌더한다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<LoggingPage />);

    expect(
      screen.getByRole("heading", { level: 1, name: "로그" }),
    ).toBeDefined();
    expect(screen.getByTestId("logging-search")).toBeDefined();
    expect(screen.getByTestId("logging-level")).toBeDefined();
    expect(screen.getByTestId("logging-range")).toBeDefined();
    expect(screen.getByTestId("logging-service")).toBeDefined();
    expect(screen.getByTestId("traces-list")).toBeDefined();
    expect(screen.getByTestId("logging-counts")).toBeDefined();
  });

  it("?traces=loading 면 표가 loading variant", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams("traces=loading"));
    render(<LoggingPage />);
    expect(
      screen.getByTestId("traces-list").getAttribute("data-state"),
    ).toBe("loading");
    expect(screen.getByTestId("traces-list-loading")).toBeDefined();
  });

  it("?traces=empty 면 표가 empty variant", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams("traces=empty"));
    render(<LoggingPage />);
    expect(
      screen.getByTestId("traces-list").getAttribute("data-state"),
    ).toBe("empty");
  });

  it("?traces=error 면 표가 error variant", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams("traces=error"));
    render(<LoggingPage />);
    expect(
      screen.getByTestId("traces-list").getAttribute("data-state"),
    ).toBe("error");
    expect(screen.getByTestId("traces-list-error")).toBeDefined();
  });

  it("검색 입력 변경 시 표가 필터링된다 (claude → llm.router 행만)", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<LoggingPage />);
    fireEvent.change(screen.getByTestId("logging-search"), {
      target: { value: "claude" },
    });
    // claude 가 들어간 메시지 (evt-1) 는 살아남고, system heartbeat (evt-17) 는 빠진다.
    expect(screen.getByTestId("traces-row-evt-1")).toBeDefined();
    expect(screen.queryByTestId("traces-row-evt-17")).toBeNull();
  });

  it("레벨 필터를 'error' 로 바꾸면 error 행만 남는다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<LoggingPage />);
    fireEvent.change(screen.getByTestId("logging-level"), {
      target: { value: "error" },
    });
    // info 행 (evt-1) 은 사라지고 error 행 (evt-7, evt-15) 은 보인다.
    expect(screen.queryByTestId("traces-row-evt-1")).toBeNull();
    expect(screen.getByTestId("traces-row-evt-7")).toBeDefined();
  });

  it("서비스 필터를 'cron' 으로 바꾸면 cron 행만 남는다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<LoggingPage />);
    fireEvent.change(screen.getByTestId("logging-service"), {
      target: { value: "cron" },
    });
    expect(screen.queryByTestId("traces-row-evt-1")).toBeNull();
    expect(screen.getByTestId("traces-row-evt-6")).toBeDefined();
  });

  it("trace_id 가 있는 행 클릭 시 TraceDetailModal 이 열린다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<LoggingPage />);
    // evt-10 (channels.telegram) — 첫 페이지 10건 안에 들어가는 trace 행.
    fireEvent.click(screen.getByTestId("traces-row-evt-10"));
    expect(screen.getByTestId("trace-detail-modal")).toBeDefined();
    expect(
      screen.getByRole("heading", {
        level: 2,
        name: "channel.telegram.dispatch",
      }),
    ).toBeDefined();
  });

  it("페이지 헤더 카운트 — 전체/warn/error 노출", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<LoggingPage />);
    const counts = screen.getByTestId("logging-counts");
    expect(within(counts).getByText(/전체/)).toBeDefined();
    expect(within(counts).getByText(/warn/)).toBeDefined();
    expect(within(counts).getByText(/error/)).toBeDefined();
  });
});
