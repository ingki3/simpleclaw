/**
 * DashboardMetrics 단위 테스트 — 4-카드 그리드 + caption 슬롯.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { DashboardMetrics } from "@/app/(shell)/dashboard/_components/DashboardMetrics";
import type { DashboardMetric } from "@/app/(shell)/dashboard/_data";

const FIXTURE: DashboardMetric[] = [
  { key: "messages24h", label: "24h 메시지", value: "347", delta: "+12%", deltaTone: "positive" },
  { key: "tokens24h", label: "24h 토큰", value: "42.1k / 18.6k", delta: "-4%", deltaTone: "negative" },
  { key: "alerts", label: "활성 알람", value: "3", caption: "webhook 외 1" },
  { key: "uptime", label: "가동 시간", value: "3d 14h", caption: "마지막 재시작 ..." },
];

describe("DashboardMetrics", () => {
  it("4개의 카드를 렌더한다", () => {
    render(<DashboardMetrics metrics={FIXTURE} />);
    for (const m of FIXTURE) {
      expect(screen.getByTestId(`metric-${m.key}`)).toBeDefined();
    }
  });

  it("값(value) 과 delta 가 화면에 노출된다", () => {
    render(<DashboardMetrics metrics={FIXTURE} />);
    expect(screen.getByText("347")).toBeDefined();
    expect(screen.getByText("42.1k / 18.6k")).toBeDefined();
    expect(screen.getByText("+12%")).toBeDefined();
    expect(screen.getByText("-4%")).toBeDefined();
  });

  it("caption 이 있으면 sparkline 슬롯으로 노출된다", () => {
    render(<DashboardMetrics metrics={FIXTURE} />);
    expect(screen.getByTestId("metric-caption-alerts").textContent).toBe(
      "webhook 외 1",
    );
    expect(screen.getByTestId("metric-caption-uptime").textContent).toBe(
      "마지막 재시작 ...",
    );
    // caption 미지정 metric 에는 sparkline 이 없음
    expect(screen.queryByTestId("metric-caption-messages24h")).toBeNull();
  });
});
