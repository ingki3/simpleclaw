/**
 * /audit 페이지 통합 단위 테스트.
 *
 * 헤더 + 4-variant + 6개 필터 + Undo 클릭 → 모달.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

const mockSearchParams = vi.fn(() => new URLSearchParams());

vi.mock("next/navigation", () => ({
  useSearchParams: () => mockSearchParams(),
  usePathname: () => "/audit",
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

import AuditPage from "@/app/(shell)/audit/page";

describe("AuditPage", () => {
  it("h1 + 6개 필터 + 표 + 카운트 섹션을 렌더한다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<AuditPage />);

    expect(
      screen.getByRole("heading", { level: 1, name: "감사" }),
    ).toBeDefined();
    expect(screen.getByTestId("audit-search")).toBeDefined();
    expect(screen.getByTestId("audit-area")).toBeDefined();
    expect(screen.getByTestId("audit-action")).toBeDefined();
    expect(screen.getByTestId("audit-actor")).toBeDefined();
    expect(screen.getByTestId("audit-range")).toBeDefined();
    expect(screen.getByTestId("audit-failed-only")).toBeDefined();
    expect(screen.getByTestId("audit-list")).toBeDefined();
    expect(screen.getByTestId("audit-counts")).toBeDefined();
  });

  it("?audit=loading 면 표가 loading variant", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams("audit=loading"));
    render(<AuditPage />);
    expect(
      screen.getByTestId("audit-list").getAttribute("data-state"),
    ).toBe("loading");
    expect(screen.getByTestId("audit-list-loading")).toBeDefined();
  });

  it("?audit=empty 면 표가 empty variant", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams("audit=empty"));
    render(<AuditPage />);
    expect(
      screen.getByTestId("audit-list").getAttribute("data-state"),
    ).toBe("empty");
  });

  it("?audit=error 면 표가 error variant", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams("audit=error"));
    render(<AuditPage />);
    expect(
      screen.getByTestId("audit-list").getAttribute("data-state"),
    ).toBe("error");
    expect(screen.getByTestId("audit-list-error")).toBeDefined();
  });

  it("영역 필터를 'secrets' 로 바꾸면 secrets entry 만 남는다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<AuditPage />);
    fireEvent.change(screen.getByTestId("audit-area"), {
      target: { value: "secrets" },
    });
    // audit-2 (secrets/openai_key) 는 살아남고, audit-1 (llm-router) 는 빠진다.
    expect(screen.getByTestId("audit-row-audit-2")).toBeDefined();
    expect(screen.queryByTestId("audit-row-audit-1")).toBeNull();
  });

  it("액션 필터를 'persona.publish' 로 바꾸면 해당 행만 남는다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<AuditPage />);
    fireEvent.change(screen.getByTestId("audit-action"), {
      target: { value: "persona.publish" },
    });
    expect(screen.queryByTestId("audit-row-audit-1")).toBeNull();
    expect(screen.getByTestId("audit-row-audit-3")).toBeDefined();
  });

  it("액터 필터를 'DesignAgent' 로 바꾸면 DesignAgent 행만 남는다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<AuditPage />);
    fireEvent.change(screen.getByTestId("audit-actor"), {
      target: { value: "DesignAgent" },
    });
    expect(screen.queryByTestId("audit-row-audit-1")).toBeNull();
    expect(screen.getByTestId("audit-row-audit-3")).toBeDefined();
  });

  it("검색 입력 변경 시 표가 필터링된다 (openai → secrets/openai_key 행만)", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<AuditPage />);
    fireEvent.change(screen.getByTestId("audit-search"), {
      target: { value: "openai" },
    });
    expect(screen.getByTestId("audit-row-audit-2")).toBeDefined();
    expect(screen.queryByTestId("audit-row-audit-3")).toBeNull();
  });

  it("실패만 보기 토글 ON — failed entry 만 남는다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<AuditPage />);
    fireEvent.click(screen.getByTestId("audit-failed-only"));
    // audit-10, audit-11 은 failed. audit-1 (applied) 은 빠진다.
    expect(screen.queryByTestId("audit-row-audit-1")).toBeNull();
    expect(screen.getByTestId("audit-row-audit-10")).toBeDefined();
    expect(screen.getByTestId("audit-row-audit-11")).toBeDefined();
  });

  it("applied 행의 Undo 클릭 시 UndoConfirmModal 이 열린다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<AuditPage />);
    // audit-1 (config.update applied) — 30d 첫 페이지 안에 들어가는 entry.
    fireEvent.click(screen.getByTestId("audit-row-audit-1-undo"));
    expect(screen.getByTestId("undo-confirm-modal")).toBeDefined();
    expect(screen.getByTestId("undo-confirm-target").textContent).toContain(
      "llm.providers.claude/timeout_ms",
    );
  });

  it("Undo 모달의 취소 → 모달 닫힘", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<AuditPage />);
    fireEvent.click(screen.getByTestId("audit-row-audit-1-undo"));
    expect(screen.getByTestId("undo-confirm-modal")).toBeDefined();
    fireEvent.click(screen.getByTestId("undo-confirm-cancel"));
    expect(screen.queryByTestId("undo-confirm-modal")).toBeNull();
  });

  it("Undo 모달의 되돌리기 → 모달 닫힘 (mutation 은 console 박제)", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<AuditPage />);
    fireEvent.click(screen.getByTestId("audit-row-audit-1-undo"));
    fireEvent.click(screen.getByTestId("undo-confirm-submit"));
    expect(screen.queryByTestId("undo-confirm-modal")).toBeNull();
  });

  it("페이지 헤더 카운트 — 전체/applied/failed/rolled-back 노출", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<AuditPage />);
    const counts = screen.getByTestId("audit-counts");
    expect(within(counts).getByText(/전체/)).toBeDefined();
    expect(within(counts).getByText(/applied/)).toBeDefined();
    expect(within(counts).getByText(/failed/)).toBeDefined();
    expect(within(counts).getByText(/rolled-back/)).toBeDefined();
  });
});
