/**
 * TraceDetailModal 단위 테스트 — 6 cases.
 *
 * 헤더/요약/Timeline/Span 인스펙터/Raw JSON/닫기 5섹션 + 기본 선택 보장.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { TraceDetailModal } from "@/app/(shell)/logging/_components/TraceDetailModal";
import { TRACE_SAMPLE } from "./_fixture";

describe("TraceDetailModal", () => {
  it("trace=null 또는 open=false 일 때 아무것도 렌더하지 않는다", () => {
    const { rerender } = render(
      <TraceDetailModal open trace={null} onClose={() => {}} />,
    );
    expect(screen.queryByTestId("trace-detail-modal")).toBeNull();

    rerender(
      <TraceDetailModal
        open={false}
        trace={TRACE_SAMPLE}
        onClose={() => {}}
      />,
    );
    expect(screen.queryByTestId("trace-detail-modal")).toBeNull();
  });

  it("헤더 — trace 이름 / 상태 / id / 트리거 시각 노출", () => {
    render(
      <TraceDetailModal open trace={TRACE_SAMPLE} onClose={() => {}} />,
    );
    expect(
      screen.getByRole("heading", { level: 2, name: TRACE_SAMPLE.name }),
    ).toBeDefined();
    expect(screen.getByTestId("trace-detail-id").textContent).toBe(
      TRACE_SAMPLE.id,
    );
    expect(screen.getByTestId("trace-detail-started-at")).toBeDefined();
  });

  it("요약 / Timeline / Raw JSON 3섹션이 모두 렌더된다", () => {
    render(
      <TraceDetailModal open trace={TRACE_SAMPLE} onClose={() => {}} />,
    );
    expect(screen.getByTestId("trace-detail-summary")).toBeDefined();
    expect(screen.getByTestId("trace-detail-span-inspector")).toBeDefined();
    expect(screen.getByTestId("trace-detail-raw")).toBeDefined();
    // Raw JSON 본문에 trace_id 가 직렬화되어 들어간다.
    expect(
      screen.getByTestId("trace-detail-raw").textContent,
    ).toContain(TRACE_SAMPLE.id);
  });

  it("초기 진입 시 첫 span 이 selected", () => {
    render(
      <TraceDetailModal open trace={TRACE_SAMPLE} onClose={() => {}} />,
    );
    const first = screen.getByTestId(
      `trace-detail-span-${TRACE_SAMPLE.spans[0].id}`,
    );
    expect(first.getAttribute("data-selected")).toBe("true");
  });

  it("다른 span 클릭 시 selected 가 이동한다", () => {
    render(
      <TraceDetailModal open trace={TRACE_SAMPLE} onClose={() => {}} />,
    );
    const second = screen.getByTestId(
      `trace-detail-span-${TRACE_SAMPLE.spans[1].id}`,
    );
    fireEvent.click(second);
    expect(second.getAttribute("data-selected")).toBe("true");
    const first = screen.getByTestId(
      `trace-detail-span-${TRACE_SAMPLE.spans[0].id}`,
    );
    expect(first.getAttribute("data-selected")).toBeNull();
  });

  it("닫기 버튼 → onClose 호출", () => {
    const onClose = vi.fn();
    render(
      <TraceDetailModal open trace={TRACE_SAMPLE} onClose={onClose} />,
    );
    fireEvent.click(screen.getByTestId("trace-detail-close"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
