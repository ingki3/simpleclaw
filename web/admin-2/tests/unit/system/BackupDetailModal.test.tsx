/**
 * BackupDetailModal 단위 테스트.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { BackupDetailModal } from "@/app/(shell)/system/_components/BackupDetailModal";
import type { BackupEntry } from "@/app/(shell)/system/_data";

const BACKUP: BackupEntry = {
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
};

describe("BackupDetailModal", () => {
  it("backup=null 이면 렌더되지 않는다", () => {
    render(
      <BackupDetailModal
        open
        backup={null}
        onClose={() => {}}
        onRestore={() => {}}
      />,
    );
    expect(screen.queryByTestId("backup-detail-modal")).toBeNull();
  });

  it("백업 메타와 무결성 해시를 노출한다", () => {
    render(
      <BackupDetailModal
        open
        backup={BACKUP}
        onClose={() => {}}
        onRestore={() => {}}
      />,
    );
    expect(screen.getByTestId("backup-detail-modal")).toBeDefined();
    expect(screen.getByTestId("backup-detail-contents").textContent).toContain(
      "config(2KB)",
    );
    expect(screen.getByTestId("backup-detail-hash").textContent).toContain(
      "sha256",
    );
  });

  it("'이 백업으로 복원' 클릭 시 onRestore 가 호출된다", () => {
    const onRestore = vi.fn();
    render(
      <BackupDetailModal
        open
        backup={BACKUP}
        onClose={() => {}}
        onRestore={onRestore}
      />,
    );
    fireEvent.click(screen.getByTestId("backup-detail-restore"));
    expect(onRestore).toHaveBeenCalledTimes(1);
    expect(onRestore.mock.calls[0][0]).toMatchObject({ id: "b-001" });
  });

  it("'다운로드' 클릭 시 onDownload 가 호출된다", () => {
    const onDownload = vi.fn();
    render(
      <BackupDetailModal
        open
        backup={BACKUP}
        onClose={() => {}}
        onRestore={() => {}}
        onDownload={onDownload}
      />,
    );
    fireEvent.click(screen.getByTestId("backup-detail-download"));
    expect(onDownload).toHaveBeenCalledTimes(1);
  });
});
