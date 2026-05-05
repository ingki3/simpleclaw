/**
 * LLM Router 페이지 통합 단위 테스트 — 섹션 렌더 + 4-variant 쿼리 + 모달 트리거.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

const mockSearchParams = vi.fn(() => new URLSearchParams());

vi.mock("next/navigation", () => ({
  useSearchParams: () => mockSearchParams(),
  usePathname: () => "/llm-router",
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

import LlmRouterPage from "@/app/(shell)/llm-router/page";

describe("LlmRouterPage", () => {
  it("h1 'LLM 라우터' 와 핵심 섹션을 모두 렌더한다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<LlmRouterPage />);

    expect(
      screen.getByRole("heading", { level: 1, name: "LLM 라우터" }),
    ).toBeDefined();
    expect(screen.getByTestId("providers-grid")).toBeDefined();
    expect(screen.getByTestId("fallback-chain")).toBeDefined();
    expect(screen.getByTestId("routing-rules")).toBeDefined();
    expect(screen.getByTestId("llm-router-default")).toBeDefined();
  });

  it("기본 상태에서 ProvidersGrid 는 default state 를 갖는다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<LlmRouterPage />);
    const grid = screen.getByTestId("providers-grid");
    expect(grid.getAttribute("data-state")).toBe("default");
    expect(screen.getByTestId("providers-grid-list")).toBeDefined();
  });

  it("?providers=loading 이면 loading variant 로 전환된다", () => {
    mockSearchParams.mockReturnValueOnce(
      new URLSearchParams("providers=loading"),
    );
    render(<LlmRouterPage />);
    const grid = screen.getByTestId("providers-grid");
    expect(grid.getAttribute("data-state")).toBe("loading");
    expect(screen.getByTestId("providers-grid-loading")).toBeDefined();
  });

  it("?providers=empty 이면 empty variant 로 전환된다", () => {
    mockSearchParams.mockReturnValueOnce(
      new URLSearchParams("providers=empty"),
    );
    render(<LlmRouterPage />);
    expect(
      screen.getByTestId("providers-grid").getAttribute("data-state"),
    ).toBe("empty");
    expect(screen.getByTestId("providers-grid-empty")).toBeDefined();
  });

  it("?providers=error 이면 error variant 로 전환된다", () => {
    mockSearchParams.mockReturnValueOnce(
      new URLSearchParams("providers=error"),
    );
    render(<LlmRouterPage />);
    expect(
      screen.getByTestId("providers-grid").getAttribute("data-state"),
    ).toBe("error");
    expect(screen.getByTestId("providers-grid-error")).toBeDefined();
  });

  it("알 수 없는 ?providers 값은 default 로 폴백한다", () => {
    mockSearchParams.mockReturnValueOnce(
      new URLSearchParams("providers=garbage"),
    );
    render(<LlmRouterPage />);
    expect(
      screen.getByTestId("providers-grid").getAttribute("data-state"),
    ).toBe("default");
  });

  it("'+ 프로바이더 추가' 클릭 시 AddProviderModal 이 열린다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<LlmRouterPage />);
    fireEvent.click(screen.getByTestId("providers-grid-add"));
    expect(screen.getByTestId("add-provider-modal")).toBeDefined();
  });

  it("provider 카드의 '편집' 클릭 시 EditProviderModal 이 열린다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<LlmRouterPage />);
    fireEvent.click(screen.getByTestId("provider-card-claude-edit"));
    expect(screen.getByTestId("edit-provider-modal")).toBeDefined();
  });

  it("규칙의 '편집' 클릭 시 RoutingRuleEditorModal 이 열린다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<LlmRouterPage />);
    fireEvent.click(screen.getByTestId("routing-rule-rule-code-edit"));
    expect(screen.getByTestId("routing-rule-modal")).toBeDefined();
  });
});
