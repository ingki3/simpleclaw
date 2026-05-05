/**
 * RecentActivityCard / RecentAlertsCard 단위 테스트.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

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

import { RecentActivityCard } from "@/app/(shell)/dashboard/_components/RecentActivityCard";
import { RecentAlertsCard } from "@/app/(shell)/dashboard/_components/RecentAlertsCard";
import type { DashboardAlert } from "@/app/(shell)/dashboard/_data";
import type { AuditEntryProps } from "@/design/molecules/AuditEntry";

describe("RecentActivityCard", () => {
  const ENTRIES: AuditEntryProps[] = [
    {
      actor: "ingki",
      action: "edit",
      target: "memory_note_limit",
      outcome: "applied",
      timestamp: "10:22",
      traceId: "abcdef0123",
    },
    {
      actor: "agent",
      action: "rotate",
      target: "telegram_token",
      outcome: "rolled-back",
      timestamp: "09:46",
      traceId: "ffeeddcc",
    },
  ];

  it("AuditEntry 항목들을 렌더하고 /audit 링크를 노출한다", () => {
    render(<RecentActivityCard entries={ENTRIES} />);
    expect(screen.getByTestId("recent-activity")).toBeDefined();
    expect(screen.getByText("memory_note_limit")).toBeDefined();
    expect(screen.getByText("telegram_token")).toBeDefined();
    expect(
      screen.getByTestId("recent-activity-view-all").getAttribute("href"),
    ).toBe("/audit");
  });

  it("entries 0개면 EmptyState 를 노출한다", () => {
    render(<RecentActivityCard entries={[]} />);
    expect(screen.getByText(/변경 이력이 없습니다/)).toBeDefined();
  });
});

describe("RecentAlertsCard", () => {
  const ALERTS: DashboardAlert[] = [
    {
      id: "a1",
      headline: "webhook · burst",
      detail: "burst dampener fired",
      tone: "warning",
      timestamp: "방금",
    },
    {
      id: "a2",
      headline: "llm · timeout",
      detail: "fallback 발동",
      tone: "error",
      timestamp: "8분 전",
    },
  ];

  it("alert 항목들을 렌더하고 /logging 링크를 노출한다", () => {
    render(<RecentAlertsCard alerts={ALERTS} />);
    expect(screen.getByTestId("recent-alerts")).toBeDefined();
    expect(screen.getByTestId("alert-a1")).toBeDefined();
    expect(screen.getByTestId("alert-a2")).toBeDefined();
    expect(screen.getByText("webhook · burst")).toBeDefined();
    expect(
      screen.getByTestId("recent-alerts-view-all").getAttribute("href"),
    ).toBe("/logging");
  });

  it("tone → 라벨 매핑이 사람이 읽는 우리말로 노출된다", () => {
    render(<RecentAlertsCard alerts={ALERTS} />);
    // warning → 주의, error → 실패
    expect(screen.getByText("주의")).toBeDefined();
    expect(screen.getByText("실패")).toBeDefined();
  });

  it("alerts 0개면 EmptyState 를 노출한다", () => {
    render(<RecentAlertsCard alerts={[]} />);
    expect(screen.getByText(/발생한 알림이 없습니다/)).toBeDefined();
  });
});
