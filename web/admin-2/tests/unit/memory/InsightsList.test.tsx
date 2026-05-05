/**
 * InsightsList 단위 테스트 — 4-variant + 액션 콜백 + cron-noise reject-only.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { InsightsList } from "@/app/(shell)/memory/_components/InsightsList";
import { INSIGHT_LIST, INSIGHT_NOISE, INSIGHT_REVIEW } from "./_fixture";

describe("InsightsList", () => {
  it("default variant — 카드가 모두 렌더된다", () => {
    render(
      <InsightsList
        state="default"
        insights={INSIGHT_LIST}
        onAccept={() => {}}
        onReject={() => {}}
        onOpenSource={() => {}}
      />,
    );
    expect(screen.getByTestId("memory-insights-cards")).toBeDefined();
    expect(screen.getByTestId(`memory-insight-${INSIGHT_REVIEW.id}`)).toBeDefined();
    expect(screen.getByTestId(`memory-insight-${INSIGHT_NOISE.id}`)).toBeDefined();
  });

  it("loading variant — aria-busy=true + 스켈레톤", () => {
    render(<InsightsList state="loading" />);
    const list = screen.getByTestId("memory-insights-list");
    expect(list.getAttribute("aria-busy")).toBe("true");
    expect(screen.getByTestId("memory-insights-list-loading")).toBeDefined();
  });

  it("empty variant — empty 메시지 (filtered=false)", () => {
    render(<InsightsList state="empty" />);
    const empty = screen.getByTestId("memory-insights-list-empty");
    expect(empty.getAttribute("data-empty-reason")).toBe("none");
  });

  it("error variant — alert role + retry 콜백", () => {
    const onRetry = vi.fn();
    render(<InsightsList state="error" onRetry={onRetry} />);
    const err = screen.getByTestId("memory-insights-list-error");
    expect(err.getAttribute("role")).toBe("alert");
    fireEvent.click(screen.getByTestId("memory-insights-list-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("default + insights=[] + searchQuery — filtered empty 안내", () => {
    render(
      <InsightsList
        state="default"
        insights={[]}
        searchQuery="없는키워드"
      />,
    );
    expect(
      screen.getByTestId("memory-insights-list-empty").getAttribute(
        "data-empty-reason",
      ),
    ).toBe("filtered");
  });

  it("채택 버튼 클릭 시 onAccept(id) 호출", () => {
    const onAccept = vi.fn();
    render(
      <InsightsList
        state="default"
        insights={INSIGHT_LIST}
        onAccept={onAccept}
      />,
    );
    fireEvent.click(
      screen.getByTestId(`memory-insight-${INSIGHT_REVIEW.id}-accept`),
    );
    expect(onAccept).toHaveBeenCalledWith(INSIGHT_REVIEW.id);
  });

  it("거절 버튼 클릭 시 onReject(insight) 호출", () => {
    const onReject = vi.fn();
    render(
      <InsightsList
        state="default"
        insights={INSIGHT_LIST}
        onReject={onReject}
      />,
    );
    fireEvent.click(
      screen.getByTestId(`memory-insight-${INSIGHT_REVIEW.id}-reject`),
    );
    expect(onReject).toHaveBeenCalledWith(INSIGHT_REVIEW);
  });

  it("출처 버튼 클릭 시 onOpenSource(insight) 호출", () => {
    const onOpenSource = vi.fn();
    render(
      <InsightsList
        state="default"
        insights={INSIGHT_LIST}
        onOpenSource={onOpenSource}
      />,
    );
    fireEvent.click(
      screen.getByTestId(`memory-insight-${INSIGHT_REVIEW.id}-source`),
    );
    expect(onOpenSource).toHaveBeenCalledWith(INSIGHT_REVIEW);
  });

  it("cron-noise 인사이트는 채택 버튼이 노출되지 않는다 (reject-only)", () => {
    render(
      <InsightsList
        state="default"
        insights={INSIGHT_LIST}
        onAccept={() => {}}
        onReject={() => {}}
      />,
    );
    expect(
      screen.queryByTestId(`memory-insight-${INSIGHT_NOISE.id}-accept`),
    ).toBeNull();
    expect(
      screen.getByTestId(`memory-insight-${INSIGHT_NOISE.id}-reject`),
    ).toBeDefined();
    expect(
      screen
        .getByTestId(`memory-insight-${INSIGHT_NOISE.id}`)
        .getAttribute("data-cron-noise"),
    ).toBe("true");
  });
});
