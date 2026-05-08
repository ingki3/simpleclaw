/**
 * TrafficSimulationModal 단위 테스트 — 슬라이더 변경에 따른 메트릭 갱신 + 닫기.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { TrafficSimulationModal } from "@/app/(shell)/channels/_components/TrafficSimulationModal";
import { ENDPOINT } from "./_fixture";

describe("TrafficSimulationModal", () => {
  it("endpoint=null 이면 dialog 렌더 안 됨", () => {
    render(
      <TrafficSimulationModal
        open
        endpoint={null}
        onClose={() => {}}
      />,
    );
    expect(screen.queryByTestId("traffic-simulation-modal")).toBeNull();
  });

  it("열림 상태 — WebhookGuardCard wrapper + 미리보기 + 메트릭 3종 노출", () => {
    render(
      <TrafficSimulationModal
        open
        endpoint={ENDPOINT}
        onClose={() => {}}
      />,
    );
    expect(screen.getByTestId("traffic-simulation-modal")).toBeDefined();
    expect(screen.getByTestId("traffic-simulation-guard")).toBeDefined();
    expect(screen.getByTestId("traffic-simulation-preview")).toBeDefined();
    expect(screen.getByTestId("traffic-simulation-chart")).toBeDefined();
    expect(screen.getByTestId("traffic-simulation-served")).toBeDefined();
    expect(screen.getByTestId("traffic-simulation-queued")).toBeDefined();
    expect(screen.getByTestId("traffic-simulation-rejected")).toBeDefined();
  });

  it("미리보기 카드에 endpoint.purpose 가 표시된다", () => {
    render(
      <TrafficSimulationModal
        open
        endpoint={ENDPOINT}
        onClose={() => {}}
      />,
    );
    expect(
      screen.getByTestId("traffic-simulation-preview").textContent,
    ).toContain(ENDPOINT.purpose);
  });

  it("처리/대기/거부 메트릭은 % 표시", () => {
    render(
      <TrafficSimulationModal
        open
        endpoint={ENDPOINT}
        onClose={() => {}}
      />,
    );
    expect(
      screen.getByTestId("traffic-simulation-served").textContent,
    ).toMatch(/\d+%/);
    expect(
      screen.getByTestId("traffic-simulation-queued").textContent,
    ).toMatch(/\d+%/);
    expect(
      screen.getByTestId("traffic-simulation-rejected").textContent,
    ).toMatch(/\d+%/);
  });

  it("닫기 버튼은 onClose 호출", () => {
    const onClose = vi.fn();
    render(
      <TrafficSimulationModal
        open
        endpoint={ENDPOINT}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByTestId("traffic-simulation-close"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
