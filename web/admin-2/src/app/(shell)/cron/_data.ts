/**
 * Cron 픽스처 — S7 (BIZ-118) 단계의 mock 데이터.
 *
 * admin.pen `euRDL` (Cron Shell) · `Y0X0SZ` (New Cron Job modal) 의 시각 spec 에
 * 1:1 매핑한다. 본 단계는 Cron 데몬 API 가 아직 미연결이므로 정적 fixture 만
 * 노출한다. 후속 sub-issue (데몬 통합 단계) 가 본 모듈만 비동기로 교체하면
 * 호출부 변경이 없도록, 컴포넌트는 fixture 를 직접 import 하지 않고
 * `getCronSnapshot()` 한 함수만 호출한다.
 */
import type { CircuitState } from "@/design/domain/CronJobRow";

/** 잡 실행 상태 — admin.pen 카드의 상태 라벨과 1:1. */
export type CronJobStatus = "idle" | "running" | "success" | "failed";

/** 잡 헬스 — admin.pen `healthy` / `circuit-open` 표기와 정렬. */
export type CronHealth = "healthy" | "degraded" | "circuit-open";

/** 마지막 실행 요약 — 행 우측 "마지막 실행" 컬럼의 SSOT. */
export interface CronRunSummary {
  /** ISO timestamp — 사람 친화 표시는 호출부에서 변환. */
  startedAt: string;
  status: CronJobStatus;
  durationMs: number;
  /** 실패 시 사유 — 한국어 한 줄. */
  error?: string;
  /** 누적 재시도 — circuit-open 판단의 한 단서. */
  retries?: number;
}

/** 등록된 크론 잡 한 건 — admin.pen `euRDL` 잡 목록 한 줄. */
export interface CronJob {
  /** 영구 키 — slug. */
  id: string;
  /** 사람이 보는 라벨 — 점(`.`) 구분 namespacing 권장. */
  name: string;
  /**
   * 스케줄 — 표준 5-필드 cron 표현식 또는 `every Nm/Nh` 친화 표기.
   * 친화 표기는 `every` 로 시작하며 `parseSchedule` 가 분 단위로 환산한다.
   */
  schedule: string;
  /** 대상 스킬/레시피 id (없으면 generic payload). */
  skillId?: string;
  /** 페이로드 JSON 문자열 — modal 의 textarea 와 1:1. */
  payload: string;
  /** 단일 실행 타임아웃 (초). */
  timeoutSeconds: number;
  /** 최대 재시도 횟수 (1 = 재시도 없음). */
  maxRetries: number;
  /** 활성/비활성 — Switch 의 SSOT. */
  enabled: boolean;
  /** 마지막 실행 — 한 번도 실행되지 않았다면 null. */
  lastRun: CronRunSummary | null;
  /** 헬스 — 카드 상태 라벨의 SSOT. */
  health: CronHealth;
  /** circuit breaker 상태. */
  circuit: CircuitState;
}

/** 24h 실행 히스토리 요약 — 페이지 하단 "실행 히스토리" 카드의 SSOT. */
export interface CronHistorySummary {
  totalRuns: number;
  success: number;
  failure: number;
  /** 평균 실행 시간 (ms). 화면은 초 단위. */
  averageMs: number;
  /** 현재 circuit-open 인 잡 수. */
  circuitOpen: number;
  /** 누적 재시도 횟수. */
  retries: number;
}

export interface CronSnapshot {
  jobs: readonly CronJob[];
  history: CronHistorySummary;
}

/**
 * Cron 화면이 그릴 모든 데이터를 한 번에 반환.
 * 실제 API 연동 시 본 함수만 비동기로 교체하면 호출부 변경이 없도록 정리.
 */
export function getCronSnapshot(): CronSnapshot {
  return {
    jobs: JOBS,
    history: HISTORY,
  };
}

function nowMinus(minutes: number): string {
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

const JOBS: readonly CronJob[] = [
  {
    id: "dreaming-cycle",
    name: "dreaming.cycle",
    schedule: "every 2h",
    skillId: "dreaming",
    payload: '{\n  "mode": "incremental"\n}',
    timeoutSeconds: 600,
    maxRetries: 2,
    enabled: true,
    lastRun: { startedAt: nowMinus(32), status: "success", durationMs: 4200 },
    health: "healthy",
    circuit: "closed",
  },
  {
    id: "memory-compact",
    name: "memory.compact",
    schedule: "0 3 * * *",
    skillId: "memory-cleanup",
    payload: '{\n  "target": "memory",\n  "mode": "compact"\n}',
    timeoutSeconds: 300,
    maxRetries: 3,
    enabled: true,
    lastRun: {
      startedAt: nowMinus(60 * 6),
      status: "success",
      durationMs: 38_000,
    },
    health: "healthy",
    circuit: "closed",
  },
  {
    id: "reflection-weekly",
    name: "reflection.weekly",
    schedule: "0 9 * * MON",
    skillId: "reflection",
    payload: '{\n  "horizon_days": 7\n}',
    timeoutSeconds: 900,
    maxRetries: 3,
    enabled: false,
    lastRun: {
      startedAt: nowMinus(60 * 24 * 3),
      status: "failed",
      durationMs: 12_000,
      error: "rate-limit",
      retries: 3,
    },
    health: "circuit-open",
    circuit: "open",
  },
  {
    id: "trace-rotation",
    name: "trace.rotation",
    schedule: "0 0 * * *",
    payload: '{\n  "retention_days": 30\n}',
    timeoutSeconds: 120,
    maxRetries: 1,
    enabled: true,
    lastRun: {
      startedAt: nowMinus(60 * 12),
      status: "success",
      durationMs: 1_200,
    },
    health: "healthy",
    circuit: "closed",
  },
  {
    id: "channel-heartbeat",
    name: "channel.heartbeat",
    schedule: "every 5m",
    payload: "{}",
    timeoutSeconds: 30,
    maxRetries: 2,
    enabled: true,
    lastRun: { startedAt: nowMinus(2), status: "success", durationMs: 320 },
    health: "healthy",
    circuit: "closed",
  },
  {
    id: "secret-rotation-watch",
    name: "secret.rotation.watch",
    schedule: "0 */6 * * *",
    payload: '{\n  "warn_days": 7\n}',
    timeoutSeconds: 60,
    maxRetries: 1,
    enabled: true,
    lastRun: {
      startedAt: nowMinus(60 * 4),
      status: "success",
      durationMs: 540,
    },
    health: "healthy",
    circuit: "closed",
  },
  {
    id: "audit-snapshot",
    name: "audit.snapshot",
    schedule: "0 4 * * *",
    payload: "{}",
    timeoutSeconds: 180,
    maxRetries: 2,
    enabled: true,
    lastRun: {
      startedAt: nowMinus(60 * 5),
      status: "running",
      durationMs: 0,
    },
    health: "healthy",
    circuit: "closed",
  },
  {
    id: "stock-watchlist-snapshot",
    name: "stock.watchlist.snapshot",
    schedule: "0 8 * * 1-5",
    skillId: "us-stock-skill",
    payload: '{\n  "tickers": ["AAPL","MSFT","NVDA"]\n}',
    timeoutSeconds: 120,
    maxRetries: 2,
    enabled: false,
    lastRun: null,
    health: "degraded",
    circuit: "half-open",
  },
];

const HISTORY: CronHistorySummary = {
  totalRuns: 43,
  success: 41,
  failure: 2,
  averageMs: 1800,
  circuitOpen: 1,
  retries: 7,
};
