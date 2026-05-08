/**
 * /system — Admin 2.0 S13 (BIZ-124).
 *
 * admin.pen `i3te7d` (Light) / `x7cT7` (Dark) 의 콘텐츠 영역을 React 로 박제한다.
 * 동일 토큰을 쓰므로 Light/Dark 페어 화면은 ThemeProvider 의 mode 에 따라 자동 전환된다 —
 * BIZ-63 채택안 (DESIGN.md §2.8).
 *
 * 구성 (위 → 아래):
 *  1) 페이지 헤더 — h1 "시스템" + 설명 + destructive "데몬 재시작" 트리거.
 *  2) row1: 시스템 정보 / 서브시스템 헬스 / 재시작 액션 / Sub-agent Pool.
 *  3) row2: Security Policy / config.yaml 스냅샷 / 테마 / 백업 · 복원.
 *  4) ConfirmRestartDialog · BackupDetailModal · RestoreConfirmModal 3 개 모달.
 *
 * BackupListCard 는 ?backups=loading|empty|error 쿼리로 4-variant (DESIGN.md §1 P3) 검증.
 * 본 단계의 모든 mutation 은 console mock — 실제 데몬 API 연동은 후속 sub-issue.
 */
"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { findAreaByPath } from "@/app/areas";
import { Button } from "@/design/atoms/Button";
import { SystemInfoCard } from "./_components/SystemInfoCard";
import { SubsystemHealthCard } from "./_components/SubsystemHealthCard";
import { RestartCard } from "./_components/RestartCard";
import { SubAgentPoolCard } from "./_components/SubAgentPoolCard";
import { SecurityPolicyCard } from "./_components/SecurityPolicyCard";
import { ConfigSnapshotCard } from "./_components/ConfigSnapshotCard";
import { ThemeCard } from "./_components/ThemeCard";
import {
  BackupListCard,
  type BackupListState,
} from "./_components/BackupListCard";
import {
  ConfirmRestartDialog,
  type RestartScope,
} from "./_components/ConfirmRestartDialog";
import { BackupDetailModal } from "./_components/BackupDetailModal";
import { RestoreConfirmModal } from "./_components/RestoreConfirmModal";
import { getSystemSnapshot, type BackupEntry } from "./_data";

const VALID_BACKUP_STATES: readonly BackupListState[] = [
  "default",
  "empty",
  "loading",
  "error",
];

export default function SystemPage() {
  return (
    <Suspense fallback={null}>
      <SystemContent />
    </Suspense>
  );
}

function SystemContent() {
  const area = findAreaByPath("/system");
  const snapshot = getSystemSnapshot();

  // ?backups=loading|empty|error 로 BackupListCard 의 4-variant 를 e2e/시각 검증.
  const params = useSearchParams();
  const requested = params.get("backups");
  const backupState: BackupListState = (
    requested && (VALID_BACKUP_STATES as readonly string[]).includes(requested)
      ? requested
      : "default"
  ) as BackupListState;

  const backupsForCard =
    backupState === "empty" ? [] : snapshot.backups;

  // 모달 상태는 페이지가 보유 — 모달 자체는 controlled.
  const [restartOpen, setRestartOpen] = useState(false);
  const [detailTarget, setDetailTarget] = useState<BackupEntry | null>(null);
  const [restoreTarget, setRestoreTarget] = useState<BackupEntry | null>(null);

  const handleRestoreLatest = () => {
    if (snapshot.backups[0]) {
      setRestoreTarget(snapshot.backups[0]);
    }
  };

  return (
    <section
      className="mx-auto flex max-w-6xl flex-col gap-6 p-8"
      data-testid="system-page"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-semibold text-(--foreground-strong)">
            {area?.label ?? "시스템"}
          </h1>
          <p className="text-sm text-(--muted-foreground)">
            데몬 상태·서브시스템 헬스·보안 정책·백업을 한 화면에서 점검합니다.
          </p>
        </div>
        <Button
          variant="destructive"
          size="sm"
          onClick={() => setRestartOpen(true)}
          data-testid="system-header-restart"
        >
          데몬 재시작
        </Button>
      </header>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        <SystemInfoCard info={snapshot.info} />
        <SubsystemHealthCard items={snapshot.subsystemHealth} />
        <RestartCard
          info={snapshot.restart}
          onRestartClick={() => setRestartOpen(true)}
        />
        <SubAgentPoolCard pool={snapshot.pool} />
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        <SecurityPolicyCard
          info={snapshot.security}
          onEdit={() => logMock("security-policy edit")}
        />
        <ConfigSnapshotCard
          info={snapshot.configSnapshot}
          onDownload={() => logMock("config-snapshot download")}
          onRestoreVersion={() => logMock("config-snapshot restore-version")}
        />
        <ThemeCard />
        <BackupListCard
          state={backupState}
          backups={backupsForCard}
          schedule={snapshot.backupSchedule}
          onSelectBackup={(b) => setDetailTarget(b)}
          onBackupNow={() => logMock("backup-now")}
          onRestoreLatest={handleRestoreLatest}
          onRetry={() => logMock("backup-list retry")}
        />
      </div>

      <ConfirmRestartDialog
        open={restartOpen}
        info={snapshot.restart}
        onClose={() => setRestartOpen(false)}
        onConfirm={(scope: RestartScope) =>
          logMock(`restart confirmed scope=${scope}`)
        }
      />

      <BackupDetailModal
        open={detailTarget !== null}
        backup={detailTarget}
        onClose={() => setDetailTarget(null)}
        onRestore={(b) => {
          setDetailTarget(null);
          setRestoreTarget(b);
        }}
        onDownload={(b) => logMock(`backup download ${b.id}`)}
      />

      <RestoreConfirmModal
        open={restoreTarget !== null}
        backup={restoreTarget}
        onClose={() => setRestoreTarget(null)}
        onConfirm={(b) => logMock(`restore confirmed ${b.id}`)}
        onDryRun={(b) => logMock(`restore dry-run ${b.id}`)}
      />
    </section>
  );
}

/** 본 단계 mutation 은 console.info 로만 박제 — 데몬 통합 단계에서 실제 호출로 교체. */
function logMock(message: string) {
  if (typeof console !== "undefined") {
    console.info(`[system] ${message}`);
  }
}
