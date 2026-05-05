/**
 * Audit 단위 테스트용 공통 픽스처.
 *
 * 시간 비교가 들어가는 필터 테스트는 결정성을 위해 "지금" 을 fixture 의 마지막
 * timestamp 로 고정한다 (Date.now 의존 X).
 */
import type { AuditEntry } from "@/app/(shell)/audit/_data";

export const ENT_APPLIED_LLM: AuditEntry = {
  id: "audit-applied-llm",
  timestamp: "2026-05-05T14:32:31.000Z",
  actor: "ingki3",
  action: "config.update",
  area: "llm-router",
  target: "llm.providers.claude/timeout_ms",
  field: "timeout_ms",
  before: "30000",
  after: "60000",
  outcome: "applied",
  traceId: "trace-cfg-001",
};

export const ENT_APPLIED_SECRET: AuditEntry = {
  id: "audit-applied-secret",
  timestamp: "2026-05-05T13:08:42.000Z",
  actor: "ingki3",
  action: "secret.rotate",
  area: "secrets",
  target: "secrets/openai_key",
  field: "value",
  after: "sk-•••••••f4z1",
  outcome: "applied",
};

export const ENT_FAILED: AuditEntry = {
  id: "audit-failed",
  timestamp: "2026-05-02T20:10:33.000Z",
  actor: "ingki3",
  action: "config.update",
  area: "llm-router",
  target: "llm.routing.rules/0",
  field: "weight",
  before: "0.3",
  after: "0.7",
  outcome: "failed",
};

export const ENT_ROLLED_BACK: AuditEntry = {
  id: "audit-rolled-back",
  timestamp: "2026-05-04T15:30:42.000Z",
  actor: "ingki3",
  action: "cron.toggle",
  area: "cron",
  target: "cron/dreaming.cycle",
  before: "true",
  after: "false",
  outcome: "rolled-back",
};

export const ENT_PENDING: AuditEntry = {
  id: "audit-pending",
  timestamp: "2026-04-29T12:00:00.000Z",
  actor: "ingki3",
  action: "secret.rotate",
  area: "secrets",
  target: "secrets/google_oauth_token",
  outcome: "pending",
};

/** 다른 actor — actor 필터 테스트용. */
export const ENT_BY_AGENT: AuditEntry = {
  id: "audit-by-agent",
  timestamp: "2026-05-05T11:55:00.000Z",
  actor: "DesignAgent",
  action: "persona.publish",
  area: "persona",
  target: "persona/agent.md",
  before: "v13",
  after: "v17",
  outcome: "applied",
};

export const ENT_OLD: AuditEntry = {
  // fixture 의 max 시각보다 60일 이전 → 24h/7d/30d 범위 필터에서 빠진다.
  id: "audit-old",
  timestamp: "2026-03-01T08:00:00.000Z",
  actor: "ingki3",
  action: "config.update",
  area: "system",
  target: "system/version",
  outcome: "applied",
};

export const ENTRIES_SAMPLE: readonly AuditEntry[] = [
  ENT_APPLIED_LLM,
  ENT_APPLIED_SECRET,
  ENT_FAILED,
  ENT_ROLLED_BACK,
  ENT_PENDING,
  ENT_BY_AGENT,
  ENT_OLD,
];

/** 페이지 단위 테스트가 PAGE_SIZE(10) 보다 많은 행을 만들고 싶을 때 사용. */
export function makeBulkEntries(count: number): AuditEntry[] {
  const entries: AuditEntry[] = [];
  for (let i = 0; i < count; i += 1) {
    entries.push({
      id: `bulk-${i}`,
      timestamp: new Date(
        Date.parse("2026-05-05T22:08:01.000Z") - i * 60_000,
      ).toISOString(),
      actor: "ingki3",
      action: "config.update",
      area: "system",
      target: `system/key-${i}`,
      after: `value-${i}`,
      outcome: "applied",
    });
  }
  return entries;
}
