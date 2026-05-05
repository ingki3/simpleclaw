/**
 * TracesList 단위 테스트 — 4-variant + 행 클릭 + 검색 하이라이트 + "더 보기".
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { TracesList } from "@/app/(shell)/logging/_components/TracesList";
import {
  EVENTS_SAMPLE,
  EVT_INFO,
  EVT_DEBUG,
  EVT_WARN,
  makeBulkEvents,
} from "./_fixture";

describe("TracesList", () => {
  it("default variant — 표에 이벤트가 모두 렌더된다", () => {
    render(<TracesList state="default" events={EVENTS_SAMPLE} />);
    expect(screen.getByTestId("traces-table")).toBeDefined();
    expect(screen.getByTestId(`traces-row-${EVT_INFO.id}`)).toBeDefined();
    expect(screen.getByTestId(`traces-row-${EVT_DEBUG.id}`)).toBeDefined();
    expect(screen.getByTestId(`traces-row-${EVT_WARN.id}`)).toBeDefined();
  });

  it("loading variant — aria-busy=true + 스켈레톤", () => {
    render(<TracesList state="loading" />);
    const list = screen.getByTestId("traces-list");
    expect(list.getAttribute("aria-busy")).toBe("true");
    expect(screen.getByTestId("traces-list-loading")).toBeDefined();
  });

  it("error variant — alert role + retry 콜백", () => {
    const onRetry = vi.fn();
    render(<TracesList state="error" onRetry={onRetry} />);
    const err = screen.getByTestId("traces-list-error");
    expect(err.getAttribute("role")).toBe("alert");
    fireEvent.click(screen.getByTestId("traces-list-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("empty variant — 안내 메시지 + reason=none", () => {
    render(<TracesList state="empty" />);
    const empty = screen.getByTestId("traces-list-empty");
    expect(empty.getAttribute("data-empty-reason")).toBe("none");
  });

  it("default variant + events=[] + searchQuery — filtered empty 안내", () => {
    render(
      <TracesList state="default" events={[]} searchQuery="없는키워드" />,
    );
    const empty = screen.getByTestId("traces-list-empty");
    expect(empty.getAttribute("data-empty-reason")).toBe("filtered");
  });

  it("trace_id 가 있는 행 클릭 시 onSelect(event) 호출", () => {
    const onSelect = vi.fn();
    render(
      <TracesList
        state="default"
        events={EVENTS_SAMPLE}
        onSelect={onSelect}
      />,
    );
    fireEvent.click(screen.getByTestId(`traces-row-${EVT_INFO.id}`));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0].id).toBe(EVT_INFO.id);
  });

  it("trace_id 가 없는 행은 클릭해도 onSelect 호출되지 않는다", () => {
    const onSelect = vi.fn();
    render(
      <TracesList
        state="default"
        events={EVENTS_SAMPLE}
        onSelect={onSelect}
      />,
    );
    fireEvent.click(screen.getByTestId(`traces-row-${EVT_DEBUG.id}`));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("level 별 Badge 라벨이 노출된다 (DEBUG/INFO/WARN/ERROR)", () => {
    render(<TracesList state="default" events={EVENTS_SAMPLE} />);
    expect(screen.getAllByText("DEBUG").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("INFO").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("WARN").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("ERROR").length).toBeGreaterThanOrEqual(1);
  });

  it("검색어가 있으면 메시지에 <mark> 하이라이트가 들어간다", () => {
    render(
      <TracesList
        state="default"
        events={EVENTS_SAMPLE}
        searchQuery="claude"
      />,
    );
    const cell = screen.getByTestId(`traces-row-${EVT_INFO.id}-message`);
    const marks = within(cell).getAllByTestId("traces-highlight");
    expect(marks.length).toBeGreaterThan(0);
    expect(marks[0].textContent?.toLowerCase()).toBe("claude");
  });

  it("검색어가 없으면 <mark> 하이라이트가 없다", () => {
    render(<TracesList state="default" events={EVENTS_SAMPLE} />);
    expect(screen.queryAllByTestId("traces-highlight").length).toBe(0);
  });

  it("trace_id 가 있는 행은 우측에 trace 표시기가 노출된다", () => {
    render(<TracesList state="default" events={EVENTS_SAMPLE} />);
    expect(
      screen.getByTestId(`traces-row-${EVT_INFO.id}-trace`),
    ).toBeDefined();
    expect(
      screen.queryByTestId(`traces-row-${EVT_DEBUG.id}-trace`),
    ).toBeNull();
  });

  it("이벤트가 PAGE_SIZE(10) 보다 많으면 첫 화면에 10건 + '더 보기' 노출", () => {
    const events = makeBulkEvents(15);
    render(<TracesList state="default" events={events} />);
    expect(screen.getByTestId("traces-list-count").textContent).toContain(
      "10 / 15",
    );
    expect(screen.getByTestId("traces-list-more")).toBeDefined();
  });

  it("'더 보기' 클릭 시 다음 PAGE_SIZE 만큼 누적 노출", () => {
    const events = makeBulkEvents(15);
    render(<TracesList state="default" events={events} />);
    fireEvent.click(screen.getByTestId("traces-list-more"));
    expect(screen.getByTestId("traces-list-count").textContent).toContain(
      "15 / 15",
    );
    expect(screen.queryByTestId("traces-list-more")).toBeNull();
  });

  it("data-state 속성이 4-variant 와 일치한다", () => {
    const { rerender } = render(<TracesList state="default" events={[]} />);
    expect(
      screen.getByTestId("traces-list").getAttribute("data-state"),
    ).toBe("default");
    rerender(<TracesList state="loading" />);
    expect(
      screen.getByTestId("traces-list").getAttribute("data-state"),
    ).toBe("loading");
    rerender(<TracesList state="empty" />);
    expect(
      screen.getByTestId("traces-list").getAttribute("data-state"),
    ).toBe("empty");
    rerender(<TracesList state="error" />);
    expect(
      screen.getByTestId("traces-list").getAttribute("data-state"),
    ).toBe("error");
  });
});
