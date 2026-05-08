/**
 * System 픽스처 — S13 (BIZ-124) 단계의 mock 데이터.
 *
 * admin.pen `i3te7d` (System Shell · Light) / `x7cT7` (System Shell · Dark) /
 * `RM5Ar` (Confirm Restart Dialog) / `vQuZm`·`I9y17` (Backup Detail Drawer) /
 * `WuWqC` (Restore Confirm Modal) 의 시각 spec 에 1:1 매핑한다.
 *
 * 본 단계는 실제 데몬 API 가 아직 미연결 상태이므로 정적 fixture 만 노출한다.
 * 후속 데몬 통합 단계가 본 모듈만 비동기로 교체하면 호출부 변경이 없도록,
 * 페이지는 fixture 를 직접 import 하지 않고 `getSystemSnapshot()` 만 호출한다.
 */
import type { StatusTone } from "@/design/atoms/StatusPill";

/** 시스템 정보 카드 (admin.pen `t9bQD`) — 5 행 key/value 메타. */
export interface SystemInfo {
  version: string;
  build: string;
  host: string;
  uptime: string;
  /** 환경 라벨 — Badge 로 표시. */
  environment: string;
}

/** 서브시스템 헬스 한 줄 (admin.pen `Z5UMJw`). */
export interface SubsystemHealth {
  key: "daemon" | "memory" | "webhook" | "cron";
  label: string;
  tone: StatusTone;
  /** 한 줄 메타 — "OK · 12ms" / "degraded · webhook 4xx". */
  detail: string;
}

/** 재시작 액션 카드 (admin.pen `k6R7yU`) 의 메타. */
export interface RestartInfo {
  /** "2026-04-29 09:11" 같은 마지막 재시작 시각. */
  lastRestart: string;
  /** "Δ 5d 6h" 같은 보조 라벨. */
  lastRestartRelative: string;
  /** 운영자 컨펌 배너 노출 여부 — production 등에서 true. */
  needsOperatorConfirm: boolean;
  /** "데몬을 재시작하시겠습니까?" 모달 본문에 들어가는 영향 요약. */
  impactSummary: string;
}

/** Sub-agent Pool · Dreaming 카드 (admin.pen `lQQaY`). */
export interface SubAgentPoolInfo {
  /** 현재 활성 / 최대 — "4 / 8". */
  poolUsage: string;
  /** "3 idle · 1 active" 같은 간단 메타. */
  idleActiveSummary: string;
  /** Wait state 라벨 — Badge 로 표시. */
  waitState: string;
  /** "다음 dreaming cycle: 약 8분 후" 같은 hint. */
  nextDreamingHint: string;
}

/** Security Policy 카드 (admin.pen `BIurh`). */
export interface SecurityPolicyInfo {
  authMode: string;
  rbacRoles: string;
  auditRetentionDays: number;
  secretRotationDays: number;
}

/** config.yaml 스냅샷 카드 (admin.pen `AznDq`). */
export interface ConfigSnapshotInfo {
  /** 활성 버전 라벨 — "v118 · 2026-05-04 14:32". */
  activeVersion: string;
  /** 짧은 yaml 발췌 — code block 으로 표시. */
  excerpt: string;
}

/** 백업 한 건 (admin.pen `k49Q3` cardBackup + `vQuZm` Backup Detail). */
export interface BackupEntry {
  /** 정렬·ID 용 — "backup-2026-05-04-0300". */
  id: string;
  /** "backup_2026-05-04_03:00.tar.gz" 형식의 파일명. */
  filename: string;
  /** ISO timestamp — UI 에서는 사람 친화 표기로 변환. */
  timestamp: string;
  /** 파일 크기 표시 — "12.4 MB". */
  sizeLabel: string;
  /** 자동/수동 트리거. */
  trigger: "auto" | "manual";
  /** 무결성 해시 — `sha256:9e2c41a7…f8b3` 형태로 일부 마스킹. */
  sha256Short: string;
  /** 포함 항목 — Backup Detail 본문에서 사용. */
  contents: BackupContent[];
}

export interface BackupContent {
  /** "config" / "persona" / "memory" / "skills". */
  label: string;
  /** "2KB" / "11.8MB" 등 사람 친화 라벨. */
  size: string;
}

/** 테마 옵션 — Light/Dark/System segmented 라디오. */
export type ThemeChoice = "light" | "dark" | "system";

export interface SystemSnapshot {
  info: SystemInfo;
  subsystemHealth: readonly SubsystemHealth[];
  restart: RestartInfo;
  pool: SubAgentPoolInfo;
  security: SecurityPolicyInfo;
  configSnapshot: ConfigSnapshotInfo;
  backups: readonly BackupEntry[];
  /** 다음 자동 백업 스케줄 — "매일 03:00 KST". */
  backupSchedule: string;
}

/**
 * System 화면이 그릴 모든 데이터를 한 번에 반환.
 *
 * 실제 API 연동 시 본 함수 시그니처만 비동기로 교체하면 호출부 변경이 없도록
 * snapshot 객체로 묶었다.
 */
export function getSystemSnapshot(): SystemSnapshot {
  return {
    info: SYSTEM_INFO,
    subsystemHealth: SUBSYSTEM_HEALTH,
    restart: RESTART_INFO,
    pool: POOL_INFO,
    security: SECURITY_INFO,
    configSnapshot: CONFIG_SNAPSHOT,
    backups: BACKUPS,
    backupSchedule: "매일 03:00 KST",
  };
}

const SYSTEM_INFO: SystemInfo = {
  version: "v0.42.1",
  build: "a3f9c12 · 2026-04-29",
  host: "claw-prd-01",
  uptime: "6일 14시간 22분",
  environment: "production",
};

const SUBSYSTEM_HEALTH: readonly SubsystemHealth[] = [
  {
    key: "daemon",
    label: "데몬",
    tone: "success",
    detail: "OK · 12ms",
  },
  {
    key: "memory",
    label: "Memory",
    tone: "success",
    detail: "OK · primary live",
  },
  {
    key: "webhook",
    label: "Webhook",
    tone: "warning",
    detail: "degraded · webhook 4xx",
  },
  {
    key: "cron",
    label: "Cron",
    tone: "success",
    detail: "OK · 3 active",
  },
];

const RESTART_INFO: RestartInfo = {
  lastRestart: "2026-04-29 09:11",
  lastRestartRelative: "Δ 5d 6h",
  needsOperatorConfirm: true,
  impactSummary:
    "실행 중인 모든 작업이 중단되며, 약 10초간 서비스가 중지됩니다.",
};

const POOL_INFO: SubAgentPoolInfo = {
  poolUsage: "4 / 8",
  idleActiveSummary: "3 idle · 1 active",
  waitState: "dreaming",
  nextDreamingHint: "BIZ-66 — 다음 dreaming cycle: 약 8분 후",
};

const SECURITY_INFO: SecurityPolicyInfo = {
  authMode: "OIDC + MFA",
  rbacRoles: "admin · operator · viewer",
  auditRetentionDays: 180,
  secretRotationDays: 90,
};

const CONFIG_SNAPSHOT: ConfigSnapshotInfo = {
  activeVersion: "v118 · 2026-05-04 14:32",
  excerpt: `routing:
  primary: claude-opus-4
  fallback: gpt-4o
memory:
  ttl_days: 90`,
};

const BACKUPS: readonly BackupEntry[] = [
  {
    id: "backup-2026-05-04-0300",
    filename: "backup_2026-05-04_03:00.tar.gz",
    timestamp: "2026-05-04T03:00:00+09:00",
    sizeLabel: "12.4 MB",
    trigger: "auto",
    sha256Short: "sha256:9e2c41a7…f8b3",
    contents: [
      { label: "config", size: "2KB" },
      { label: "persona", size: "48KB" },
      { label: "memory", size: "11.8MB" },
      { label: "skills", size: "0.6MB" },
    ],
  },
  {
    id: "backup-2026-05-03-0300",
    filename: "backup_2026-05-03_03:00.tar.gz",
    timestamp: "2026-05-03T03:00:00+09:00",
    sizeLabel: "12.3 MB",
    trigger: "auto",
    sha256Short: "sha256:b1d8e5f2…aa49",
    contents: [
      { label: "config", size: "2KB" },
      { label: "persona", size: "47KB" },
      { label: "memory", size: "11.7MB" },
      { label: "skills", size: "0.6MB" },
    ],
  },
  {
    id: "backup-2026-05-02-0300",
    filename: "backup_2026-05-02_03:00.tar.gz",
    timestamp: "2026-05-02T03:00:00+09:00",
    sizeLabel: "12.1 MB",
    trigger: "auto",
    sha256Short: "sha256:c4f02178…12a0",
    contents: [
      { label: "config", size: "2KB" },
      { label: "persona", size: "46KB" },
      { label: "memory", size: "11.5MB" },
      { label: "skills", size: "0.6MB" },
    ],
  },
  {
    id: "backup-2026-05-01-1130-manual",
    filename: "backup_2026-05-01_11:30_manual.tar.gz",
    timestamp: "2026-05-01T11:30:00+09:00",
    sizeLabel: "11.9 MB",
    trigger: "manual",
    sha256Short: "sha256:7720e9c5…0f27",
    contents: [
      { label: "config", size: "2KB" },
      { label: "persona", size: "45KB" },
      { label: "memory", size: "11.3MB" },
      { label: "skills", size: "0.6MB" },
    ],
  },
];
