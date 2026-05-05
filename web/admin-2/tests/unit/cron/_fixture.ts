/**
 * Cron 단위 테스트용 공통 픽스처.
 */
import type { CronJob } from "@/app/(shell)/cron/_data";

export const JOB_ENABLED: CronJob = {
  id: "memory-compact",
  name: "memory.compact",
  schedule: "0 3 * * *",
  skillId: "memory-cleanup",
  payload: '{"target":"memory"}',
  timeoutSeconds: 300,
  maxRetries: 3,
  enabled: true,
  lastRun: {
    startedAt: "2026-05-05T03:00:00.000Z",
    status: "success",
    durationMs: 38_000,
  },
  health: "healthy",
  circuit: "closed",
};

export const JOB_FAILED: CronJob = {
  id: "reflection-weekly",
  name: "reflection.weekly",
  schedule: "0 9 * * MON",
  skillId: "reflection",
  payload: "{}",
  timeoutSeconds: 600,
  maxRetries: 3,
  enabled: false,
  lastRun: {
    startedAt: "2026-05-02T09:00:00.000Z",
    status: "failed",
    durationMs: 12_000,
    error: "rate-limit",
    retries: 3,
  },
  health: "circuit-open",
  circuit: "open",
};

export const JOB_LIST: readonly CronJob[] = [JOB_ENABLED, JOB_FAILED];
