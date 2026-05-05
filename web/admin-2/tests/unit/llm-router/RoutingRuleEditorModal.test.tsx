/**
 * RoutingRuleEditorModal 단위 테스트 — prefill / 우선순위 ↑↓ / dry-run.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { RoutingRuleEditorModal } from "@/app/(shell)/llm-router/_components/RoutingRuleEditorModal";
import type { RoutingRule } from "@/app/(shell)/llm-router/_data";

const RULE: RoutingRule = {
  id: "rule-code",
  name: "코드 작업 → Opus",
  trigger: "intent: code",
  providerOrder: [
    {
      providerId: "claude",
      label: "Claude · opus",
      rate: "$15/MTok",
      latency: "~2.4s",
    },
    {
      providerId: "claude",
      label: "Claude · sonnet",
      rate: "$3/MTok",
      latency: "~1.6s",
    },
    {
      providerId: "gemini",
      label: "Gemini · pro",
      rate: "$1.25/MTok",
      latency: "~1.2s",
      badge: "fallback",
    },
  ],
  dailyBudgetUsd: 50,
};

describe("RoutingRuleEditorModal", () => {
  it("rule=null 이면 dialog 가 렌더되지 않는다", () => {
    render(
      <RoutingRuleEditorModal
        open
        rule={null}
        onClose={() => {}}
        onDryRun={() => {}}
      />,
    );
    expect(screen.queryByTestId("routing-rule-modal")).toBeNull();
  });

  it("rule 이 있으면 prefill 후 폼 필드를 노출한다", () => {
    render(
      <RoutingRuleEditorModal
        open
        rule={RULE}
        onClose={() => {}}
        onDryRun={() => {}}
      />,
    );
    expect(screen.getByTestId("routing-rule-modal")).toBeDefined();
    expect((screen.getByTestId("rule-name") as HTMLInputElement).value).toBe(
      "코드 작업 → Opus",
    );
    expect(
      (screen.getByTestId("rule-trigger") as HTMLInputElement).value,
    ).toBe("intent: code");
    expect(screen.getByTestId("rule-provider-order")).toBeDefined();
  });

  it("provider ↓ 버튼 클릭 시 순서가 교체된다", () => {
    render(
      <RoutingRuleEditorModal
        open
        rule={RULE}
        onClose={() => {}}
        onDryRun={() => {}}
      />,
    );
    // 첫 항목을 한 칸 내림 → 두 번째 자리로.
    fireEvent.click(screen.getByTestId("rule-provider-0-down"));
    const second = screen.getByTestId("rule-provider-1");
    expect(second.textContent).toContain("Claude · opus");
  });

  it("'적용 (dry-run)' 클릭 시 onDryRun 호출, 폼값 전달", () => {
    const onDryRun = vi.fn();
    render(
      <RoutingRuleEditorModal
        open
        rule={RULE}
        onClose={() => {}}
        onDryRun={onDryRun}
      />,
    );
    fireEvent.click(screen.getByTestId("routing-rule-dryrun"));
    expect(onDryRun).toHaveBeenCalledTimes(1);
    expect(onDryRun.mock.calls[0]?.[0]).toMatchObject({
      id: "rule-code",
      name: "코드 작업 → Opus",
    });
  });

  it("취소 버튼은 onClose 만 호출한다", () => {
    const onClose = vi.fn();
    const onDryRun = vi.fn();
    render(
      <RoutingRuleEditorModal
        open
        rule={RULE}
        onClose={onClose}
        onDryRun={onDryRun}
      />,
    );
    fireEvent.click(screen.getByTestId("routing-rule-cancel"));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onDryRun).not.toHaveBeenCalled();
  });
});
