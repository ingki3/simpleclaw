/**
 * WebhookList 단위 테스트 — 4-variant + endpoint 토글 + 편집 콜백.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import {
  WebhookList,
  type WebhookListState,
} from "@/app/(shell)/channels/_components/WebhookList";
import { ENDPOINT, POLICY } from "./_fixture";

function renderList(
  overrides?: Partial<Parameters<typeof WebhookList>[0]>,
) {
  const props = {
    state: "default" as WebhookListState,
    webhooks: {
      policy: POLICY,
      endpoints: [ENDPOINT],
      reqLast24hTotal: 100,
      errorRate24h: 0.01,
    },
    policy: POLICY,
    onPolicyChange: vi.fn(),
    onToggleEndpoint: vi.fn(),
    onEditEndpoint: vi.fn(),
    onRetry: vi.fn(),
    ...overrides,
  };
  render(<WebhookList {...props} />);
  return props;
}

describe("WebhookList", () => {
  it("default state — endpoint 표를 그린다", () => {
    renderList();
    expect(
      screen.getByTestId("webhook-list").getAttribute("data-state"),
    ).toBe("default");
    expect(screen.getByTestId("webhook-list-table")).toBeDefined();
    expect(screen.getByTestId(`webhook-endpoint-${ENDPOINT.id}`)).toBeDefined();
  });

  it("loading state — aria-busy + 스켈레톤 노출", () => {
    renderList({ state: "loading" });
    const list = screen.getByTestId("webhook-list");
    expect(list.getAttribute("aria-busy")).toBe("true");
    expect(screen.getByTestId("webhook-list-loading")).toBeDefined();
  });

  it("empty state — EmptyState 노출", () => {
    renderList({ state: "empty" });
    expect(screen.getByTestId("webhook-list-empty")).toBeDefined();
  });

  it("error state — alert + 재시도 트리거", () => {
    const props = renderList({ state: "error" });
    const err = screen.getByTestId("webhook-list-error");
    expect(err.getAttribute("role")).toBe("alert");
    fireEvent.click(screen.getByTestId("webhook-list-retry"));
    expect(props.onRetry).toHaveBeenCalledTimes(1);
  });

  it("endpoint Switch 토글이 onToggleEndpoint 를 호출한다", () => {
    const props = renderList();
    fireEvent.click(
      screen.getByTestId(`webhook-endpoint-${ENDPOINT.id}-toggle`),
    );
    expect(props.onToggleEndpoint).toHaveBeenCalledWith(ENDPOINT.id, false);
  });

  it("endpoint '편집' 버튼이 onEditEndpoint 를 호출한다", () => {
    const props = renderList();
    fireEvent.click(
      screen.getByTestId(`webhook-endpoint-${ENDPOINT.id}-edit`),
    );
    expect(props.onEditEndpoint).toHaveBeenCalledWith(ENDPOINT);
  });

  it("정책 입력 변경이 onPolicyChange 를 호출한다", () => {
    const props = renderList();
    fireEvent.change(screen.getByTestId("webhook-policy-rate-limit"), {
      target: { value: "100" },
    });
    expect(props.onPolicyChange).toHaveBeenCalledWith({
      ...POLICY,
      rateLimitPerSec: 100,
    });
  });

  it("default state 에서 헤더에 endpoint 수 / 24h 메타가 노출된다", () => {
    renderList();
    const meta = screen.getByTestId("webhook-list-meta");
    expect(meta.textContent).toContain("1 endpoint");
    expect(meta.textContent).toContain("100");
  });
});
