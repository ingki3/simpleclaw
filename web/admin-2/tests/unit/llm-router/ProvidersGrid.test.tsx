/**
 * ProvidersGrid 단위 테스트 — 4-variant + onAdd/onEdit/onRetry 콜백.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { ProvidersGrid } from "@/app/(shell)/llm-router/_components/ProvidersGrid";
import type { RouterProvider } from "@/app/(shell)/llm-router/_data";

const PROVIDER: RouterProvider = {
  id: "claude",
  name: "claude",
  apiType: "anthropic",
  model: "claude-opus-4-6",
  baseUrl: "api.anthropic.com/v1",
  apiKeyMasked: "sk-ant-••••3a72",
  keyringName: "claude_api_key",
  isDefault: true,
  inFallbackChain: true,
  fallbackPriority: 0,
  health: {
    tone: "success",
    label: "정상 · 350ms avg",
    avgLatencyMs: 350,
    tokens24h: "44.0k",
  },
};

describe("ProvidersGrid", () => {
  it("default state — 카드 그리드 + Add 버튼 노출", () => {
    const onAdd = vi.fn();
    const onEdit = vi.fn();
    render(
      <ProvidersGrid
        state="default"
        providers={[PROVIDER]}
        onAdd={onAdd}
        onEdit={onEdit}
      />,
    );
    const grid = screen.getByTestId("providers-grid");
    expect(grid.getAttribute("data-state")).toBe("default");
    expect(screen.getByTestId("provider-card-claude")).toBeDefined();
    expect(screen.getByTestId("providers-grid-add")).toBeDefined();
  });

  it("default + providers=[] 면 empty 폴백", () => {
    render(
      <ProvidersGrid
        state="default"
        providers={[]}
        onAdd={() => {}}
        onEdit={() => {}}
      />,
    );
    expect(screen.getByTestId("providers-grid-empty")).toBeDefined();
  });

  it("loading state — 스켈레톤 + aria-busy", () => {
    render(
      <ProvidersGrid state="loading" onAdd={() => {}} onEdit={() => {}} />,
    );
    const grid = screen.getByTestId("providers-grid");
    expect(grid.getAttribute("aria-busy")).toBe("true");
    expect(screen.getByTestId("providers-grid-loading")).toBeDefined();
  });

  it("empty state — EmptyState + Add CTA 동작", () => {
    const onAdd = vi.fn();
    render(<ProvidersGrid state="empty" onAdd={onAdd} onEdit={() => {}} />);
    expect(screen.getByTestId("providers-grid-empty")).toBeDefined();
    fireEvent.click(screen.getByText("프로바이더 추가"));
    expect(onAdd).toHaveBeenCalledTimes(1);
  });

  it("error state — alert role + retry 버튼 동작", () => {
    const onRetry = vi.fn();
    render(
      <ProvidersGrid
        state="error"
        errorMessage="네트워크 오류"
        onRetry={onRetry}
        onAdd={() => {}}
        onEdit={() => {}}
      />,
    );
    const errorBox = screen.getByTestId("providers-grid-error");
    expect(errorBox.getAttribute("role")).toBe("alert");
    expect(errorBox.textContent).toContain("네트워크 오류");
    fireEvent.click(screen.getByTestId("providers-grid-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("provider 카드의 '편집' 클릭 시 onEdit 가 provider 와 함께 호출", () => {
    const onEdit = vi.fn();
    render(
      <ProvidersGrid
        state="default"
        providers={[PROVIDER]}
        onAdd={() => {}}
        onEdit={onEdit}
      />,
    );
    fireEvent.click(screen.getByTestId("provider-card-claude-edit"));
    expect(onEdit).toHaveBeenCalledWith(PROVIDER);
  });
});
