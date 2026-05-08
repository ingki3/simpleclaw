/**
 * RestoreConfirmModal 단위 테스트 — 영향 범위 + 5단계 stepper + dry-run.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { RestoreConfirmModal } from "@/app/(shell)/system/_components/RestoreConfirmModal";
import type { BackupEntry } from "@/app/(shell)/system/_data";

const BACKUP: BackupEntry = {
  id: "b-001",
  filename: "backup_a.tar.gz",
  timestamp: "2026-05-04T03:00:00+09:00",
  sizeLabel: "12.4 MB",
  trigger: "auto",
  sha256Short: "sha256:1234…abcd",
  contents: [{ label: "config", size: "2KB" }],
};

describe("RestoreConfirmModal", () => {
  it("backup=null 이면 렌더되지 않는다", () => {
    render(
      <RestoreConfirmModal
        open
        backup={null}
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.queryByTestId("restore-confirm-modal")).toBeNull();
  });

  it("영향 범위 + 5단계 stepper 를 모두 노출한다", () => {
    render(
      <RestoreConfirmModal
        open
        backup={BACKUP}
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.getByTestId("restore-confirm-impact")).toBeDefined();
    for (const k of ["stop", "snapshot", "restore", "integrity", "start"]) {
      expect(screen.getByTestId(`restore-confirm-step-${k}`)).toBeDefined();
    }
  });

  it("dry-run 클릭 시 onDryRun 이 호출되고 결과 안내가 뜬다", () => {
    const onDryRun = vi.fn();
    render(
      <RestoreConfirmModal
        open
        backup={BACKUP}
        onClose={() => {}}
        onConfirm={() => {}}
        onDryRun={onDryRun}
      />,
    );
    fireEvent.click(screen.getByTestId("restore-confirm-dryrun"));
    expect(onDryRun).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("restore-confirm-dryrun-result")).toBeDefined();
  });

  it("ConfirmGate 가 노출된다 — keyword='restore' 안내", () => {
    render(
      <RestoreConfirmModal
        open
        backup={BACKUP}
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    // ConfirmGate 의 키워드 코드 블록 — description 영역에 노출됨.
    expect(screen.getByTestId("restore-confirm-description")).toBeDefined();
  });
});
