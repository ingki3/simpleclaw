/**
 * BackupListCard — admin.pen `k49Q3` (cardBackup) + Patch E `vQuZm` 인벤토리 박제.
 *
 * 본 카드는 두 단계의 정보를 한 곳에 합친다:
 *  1) "마지막 백업" + "스케줄" 메타 — admin.pen `k49Q3` 의 상단 row 2개.
 *  2) 백업 이력 목록 — 시간순 + 사이즈 + 액션. BIZ-124 DoD 1번 항목과 매칭.
 *
 * 본 단계는 4-variant (default / empty / loading / error) 모두를 지원한다 —
 * DESIGN.md §1 Principle 3 / §4.6.
 *
 * 행 클릭 시 부모가 BackupDetailModal 을 연다. "지금 백업" / "복원…" 두 액션은
 * 각각 즉시 실행 / RestoreConfirmModal 진입.
 */
"use client";

import { Badge } from "@/design/atoms/Badge";
import { Button } from "@/design/atoms/Button";
import { EmptyState } from "@/design/molecules/EmptyState";
import { StatusPill } from "@/design/atoms/StatusPill";
import { cn } from "@/lib/cn";
import type { BackupEntry } from "../_data";

export type BackupListState = "default" | "empty" | "loading" | "error";

interface BackupListCardProps {
  state: BackupListState;
  backups?: readonly BackupEntry[];
  /** 다음 자동 백업 스케줄 — "매일 03:00 KST" 등. */
  schedule: string;
  /** 행 클릭 시 — Backup Detail modal 열기. */
  onSelectBackup: (backup: BackupEntry) => void;
  /** "지금 백업" 클릭 — 부모가 즉시 트리거. */
  onBackupNow: () => void;
  /** "복원…" 클릭 (가장 최근 백업으로 빠른 복원 진입). */
  onRestoreLatest: () => void;
  /** error variant 의 사람 친화 메시지. */
  errorMessage?: string;
  onRetry?: () => void;
  className?: string;
}

const SKELETON_ROWS = 3;

export function BackupListCard({
  state,
  backups = [],
  schedule,
  onSelectBackup,
  onBackupNow,
  onRestoreLatest,
  errorMessage = "백업 목록을 불러오지 못했습니다.",
  onRetry,
  className,
}: BackupListCardProps) {
  const latest = backups[0];
  return (
    <section
      data-testid="backup-list-card"
      data-state={state}
      aria-label="백업 · 복원"
      aria-busy={state === "loading" || undefined}
      className={cn(
        "flex flex-col gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-5 shadow-(--shadow-sm)",
        className,
      )}
    >
      <header className="flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold text-(--foreground-strong)">
          백업 · 복원
        </h2>
        <Badge tone="neutral" data-testid="backup-list-schedule">
          {schedule}
        </Badge>
      </header>

      {state === "default" && latest ? (
        <div className="flex items-center justify-between gap-3 text-xs">
          <span className="text-(--muted-foreground)">마지막 백업</span>
          <span className="font-mono text-(--foreground)">
            {formatTimestamp(latest.timestamp)} · {latest.sizeLabel}
          </span>
        </div>
      ) : null}

      <p className="text-[11px] font-medium uppercase tracking-wide text-(--muted-foreground)">
        히스토리
      </p>

      {state === "loading" ? <BackupListLoading /> : null}
      {state === "error" ? (
        <BackupListError message={errorMessage} onRetry={onRetry} />
      ) : null}
      {state === "empty" ? (
        <BackupListEmpty onBackupNow={onBackupNow} />
      ) : null}
      {state === "default" ? (
        backups.length === 0 ? (
          <BackupListEmpty onBackupNow={onBackupNow} />
        ) : (
          <ul
            data-testid="backup-list"
            className="flex flex-col divide-y divide-(--border)"
          >
            {backups.map((b) => (
              <li
                key={b.id}
                data-testid={`backup-row-${b.id}`}
                className="flex items-center justify-between gap-3 py-2 first:pt-0 last:pb-0"
              >
                <button
                  type="button"
                  onClick={() => onSelectBackup(b)}
                  data-testid={`backup-row-${b.id}-open`}
                  className="flex flex-1 items-center justify-between gap-3 text-left text-xs hover:text-(--primary)"
                >
                  <span className="flex items-center gap-2">
                    <span className="font-mono text-(--foreground)">
                      {formatTimestamp(b.timestamp)}
                    </span>
                    {b.trigger === "manual" ? (
                      <Badge tone="info" size="sm">
                        manual
                      </Badge>
                    ) : null}
                  </span>
                  <span className="font-mono text-(--muted-foreground)">
                    {b.sizeLabel}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )
      ) : null}

      {state === "default" || state === "empty" ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="primary"
            size="sm"
            onClick={onBackupNow}
            data-testid="backup-list-backup-now"
          >
            지금 백업
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={onRestoreLatest}
            disabled={!latest}
            data-testid="backup-list-restore"
          >
            복원…
          </Button>
        </div>
      ) : null}
    </section>
  );
}

function BackupListLoading() {
  return (
    <ul
      data-testid="backup-list-loading"
      role="status"
      aria-label="백업 목록 로딩 중"
      className="flex flex-col divide-y divide-(--border)"
    >
      {Array.from({ length: SKELETON_ROWS }).map((_, i) => (
        <li
          key={i}
          className="flex animate-pulse items-center justify-between gap-3 py-2 first:pt-0 last:pb-0"
        >
          <span className="h-3 w-32 rounded-(--radius-sm) bg-(--surface)" />
          <span className="h-3 w-12 rounded-(--radius-sm) bg-(--surface)" />
        </li>
      ))}
    </ul>
  );
}

function BackupListEmpty({ onBackupNow }: { onBackupNow: () => void }) {
  return (
    <div data-testid="backup-list-empty">
      <EmptyState
        title="백업 이력이 없습니다"
        description="최초 1회 백업을 생성하세요. 이후 스케줄에 따라 자동 백업이 누적됩니다."
        action={
          <Button size="sm" variant="primary" onClick={onBackupNow}>
            지금 백업
          </Button>
        }
      />
    </div>
  );
}

function BackupListError({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      data-testid="backup-list-error"
      className="flex flex-col items-start gap-2 rounded-(--radius-m) border border-(--color-error) bg-(--color-error-bg) p-4 text-sm"
    >
      <div className="flex items-center gap-2">
        <StatusPill tone="error">실패</StatusPill>
        <span className="font-medium text-(--color-error)">{message}</span>
      </div>
      <p className="text-xs text-(--muted-foreground)">
        잠시 후 자동 재시도 — 즉시 다시 시도하려면 아래 버튼을 누르세요.
      </p>
      {onRetry ? (
        <Button
          size="sm"
          variant="secondary"
          onClick={onRetry}
          data-testid="backup-list-retry"
        >
          다시 불러오기
        </Button>
      ) : null}
    </div>
  );
}

/** ISO timestamp → "YYYY-MM-DD HH:mm" 표기. 실패 시 원문 반환. */
export function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
