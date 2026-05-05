/**
 * System 페이지 통합 단위 테스트 — 8 카드 + 모달 트리거 + 4-variant 쿼리.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

const mockSearchParams = vi.fn(() => new URLSearchParams());

vi.mock("next/navigation", () => ({
  useSearchParams: () => mockSearchParams(),
  usePathname: () => "/system",
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

import SystemPage from "@/app/(shell)/system/page";
import { ThemeProvider } from "@/design/ThemeProvider";

function renderPage() {
  return render(
    <ThemeProvider>
      <SystemPage />
    </ThemeProvider>,
  );
}

describe("SystemPage", () => {
  it("h1 '시스템' 과 8 카드를 모두 렌더한다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    renderPage();

    expect(
      screen.getByRole("heading", { level: 1, name: "시스템" }),
    ).toBeDefined();

    for (const id of [
      "system-info-card",
      "subsystem-health-card",
      "restart-card",
      "sub-agent-pool-card",
      "security-policy-card",
      "config-snapshot-card",
      "theme-card",
      "backup-list-card",
    ]) {
      expect(screen.getByTestId(id)).toBeDefined();
    }
  });

  it("기본 상태에서 BackupListCard 는 default state 를 갖는다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    renderPage();
    const card = screen.getByTestId("backup-list-card");
    expect(card.getAttribute("data-state")).toBe("default");
    expect(screen.getByTestId("backup-list")).toBeDefined();
  });

  it("?backups=loading 이면 loading variant 로 전환된다", () => {
    mockSearchParams.mockReturnValueOnce(
      new URLSearchParams("backups=loading"),
    );
    renderPage();
    const card = screen.getByTestId("backup-list-card");
    expect(card.getAttribute("data-state")).toBe("loading");
    expect(card.getAttribute("aria-busy")).toBe("true");
    expect(screen.getByTestId("backup-list-loading")).toBeDefined();
  });

  it("?backups=empty 이면 empty variant 로 전환된다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams("backups=empty"));
    renderPage();
    expect(
      screen.getByTestId("backup-list-card").getAttribute("data-state"),
    ).toBe("empty");
    expect(screen.getByTestId("backup-list-empty")).toBeDefined();
  });

  it("?backups=error 이면 error variant 로 전환된다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams("backups=error"));
    renderPage();
    const err = screen.getByTestId("backup-list-error");
    expect(err).toBeDefined();
    expect(err.getAttribute("role")).toBe("alert");
  });

  it("알 수 없는 ?backups 값은 default 로 폴백한다", () => {
    mockSearchParams.mockReturnValueOnce(
      new URLSearchParams("backups=garbage"),
    );
    renderPage();
    expect(
      screen.getByTestId("backup-list-card").getAttribute("data-state"),
    ).toBe("default");
  });

  it("헤더의 '데몬 재시작' 클릭 시 ConfirmRestartDialog 가 열린다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    renderPage();
    fireEvent.click(screen.getByTestId("system-header-restart"));
    expect(screen.getByTestId("confirm-restart-dialog")).toBeDefined();
  });

  it("RestartCard 의 트리거도 동일 다이얼로그를 연다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    renderPage();
    fireEvent.click(screen.getByTestId("restart-card-trigger"));
    expect(screen.getByTestId("confirm-restart-dialog")).toBeDefined();
  });

  it("백업 행 클릭 시 BackupDetailModal 이 열린다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    renderPage();
    fireEvent.click(
      screen.getByTestId("backup-row-backup-2026-05-04-0300-open"),
    );
    expect(screen.getByTestId("backup-detail-modal")).toBeDefined();
  });

  it("BackupListCard '복원…' 클릭 시 RestoreConfirmModal 이 열린다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    renderPage();
    fireEvent.click(screen.getByTestId("backup-list-restore"));
    expect(screen.getByTestId("restore-confirm-modal")).toBeDefined();
  });

  it("BackupDetailModal 의 '이 백업으로 복원' 클릭 시 detail 이 닫히고 RestoreConfirmModal 이 열린다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    renderPage();
    fireEvent.click(
      screen.getByTestId("backup-row-backup-2026-05-04-0300-open"),
    );
    fireEvent.click(screen.getByTestId("backup-detail-restore"));
    expect(screen.queryByTestId("backup-detail-modal")).toBeNull();
    expect(screen.getByTestId("restore-confirm-modal")).toBeDefined();
  });
});
