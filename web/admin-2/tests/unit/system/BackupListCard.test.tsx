/**
 * BackupListCard 단위 테스트 — 4-variant + 행 액션.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { BackupListCard } from "@/app/(shell)/system/_components/BackupListCard";
import type { BackupEntry } from "@/app/(shell)/system/_data";

const SAMPLE: readonly BackupEntry[] = [
  {
    id: "b-001",
    filename: "backup_a.tar.gz",
    timestamp: "2026-05-04T03:00:00+09:00",
    sizeLabel: "12.4 MB",
    trigger: "auto",
    sha256Short: "sha256:1234…abcd",
    contents: [
      { label: "config", size: "2KB" },
      { label: "memory", size: "11MB" },
    ],
  },
  {
    id: "b-002",
    filename: "backup_b_manual.tar.gz",
    timestamp: "2026-05-03T11:30:00+09:00",
    sizeLabel: "11.9 MB",
    trigger: "manual",
    sha256Short: "sha256:5678…efgh",
    contents: [{ label: "config", size: "2KB" }],
  },
];

const handlers = () => ({
  onSelectBackup: vi.fn(),
  onBackupNow: vi.fn(),
  onRestoreLatest: vi.fn(),
  onRetry: vi.fn(),
});

describe("BackupListCard", () => {
  it("default state — backups 를 행으로 렌더하고 마지막 백업 메타를 노출한다", () => {
    const h = handlers();
    render(
      <BackupListCard
        state="default"
        backups={SAMPLE}
        schedule="매일 03:00 KST"
        {...h}
      />,
    );
    expect(screen.getByTestId("backup-list")).toBeDefined();
    expect(screen.getByTestId("backup-row-b-001")).toBeDefined();
    expect(screen.getByTestId("backup-row-b-002")).toBeDefined();
    // schedule badge
    expect(screen.getByTestId("backup-list-schedule").textContent).toContain(
      "매일 03:00 KST",
    );
  });

  it("loading state — aria-busy=true 와 skeleton 을 노출한다", () => {
    const h = handlers();
    render(
      <BackupListCard
        state="loading"
        backups={[]}
        schedule="매일 03:00 KST"
        {...h}
      />,
    );
    const card = screen.getByTestId("backup-list-card");
    expect(card.getAttribute("aria-busy")).toBe("true");
    expect(screen.getByTestId("backup-list-loading")).toBeDefined();
  });

  it("empty state — EmptyState + '지금 백업' CTA 를 노출한다", () => {
    const h = handlers();
    render(
      <BackupListCard
        state="empty"
        backups={[]}
        schedule="매일 03:00 KST"
        {...h}
      />,
    );
    expect(screen.getByTestId("backup-list-empty")).toBeDefined();
    expect(screen.getByTestId("backup-list-backup-now")).toBeDefined();
  });

  it("error state — alert role + 재시도 버튼을 노출한다", () => {
    const h = handlers();
    render(
      <BackupListCard
        state="error"
        backups={[]}
        schedule="매일 03:00 KST"
        {...h}
      />,
    );
    const err = screen.getByTestId("backup-list-error");
    expect(err.getAttribute("role")).toBe("alert");
    fireEvent.click(screen.getByTestId("backup-list-retry"));
    expect(h.onRetry).toHaveBeenCalledTimes(1);
  });

  it("행 클릭 시 onSelectBackup 콜백이 호출된다", () => {
    const h = handlers();
    render(
      <BackupListCard
        state="default"
        backups={SAMPLE}
        schedule="매일 03:00 KST"
        {...h}
      />,
    );
    fireEvent.click(screen.getByTestId("backup-row-b-001-open"));
    expect(h.onSelectBackup).toHaveBeenCalledTimes(1);
    expect(h.onSelectBackup.mock.calls[0][0]).toMatchObject({ id: "b-001" });
  });

  it("'복원…' 클릭 시 onRestoreLatest 가 호출되고, backups 가 비면 disabled", () => {
    const h = handlers();
    const { rerender } = render(
      <BackupListCard
        state="default"
        backups={SAMPLE}
        schedule="매일 03:00 KST"
        {...h}
      />,
    );
    fireEvent.click(screen.getByTestId("backup-list-restore"));
    expect(h.onRestoreLatest).toHaveBeenCalledTimes(1);

    rerender(
      <BackupListCard
        state="default"
        backups={[]}
        schedule="매일 03:00 KST"
        {...h}
      />,
    );
    expect(
      (screen.getByTestId("backup-list-restore") as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});
