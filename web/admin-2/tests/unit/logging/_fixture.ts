/**
 * Logging 단위 테스트용 공통 픽스처.
 *
 * 시간 비교가 들어가는 필터 테스트는 결정성을 위해 "지금" 을 fixture 의 마지막
 * timestamp 로 고정한다 (Date.now 의존 X).
 */
import type { LogEvent, TraceDetail } from "@/app/(shell)/logging/_data";

export const EVT_INFO: LogEvent = {
  id: "evt-info",
  timestamp: "2026-05-05T22:00:12.000Z",
  level: "info",
  service: "llm.router",
  message: "dispatched claude-opus-4-6 (1820 in / 412 out)",
  traceId: "trace-llm-001",
};

export const EVT_DEBUG: LogEvent = {
  id: "evt-debug",
  timestamp: "2026-05-05T22:00:13.000Z",
  level: "debug",
  service: "llm.router",
  message: "fallback chain primed: claude → openai → gemini",
};

export const EVT_WARN: LogEvent = {
  id: "evt-warn",
  timestamp: "2026-05-05T22:02:30.000Z",
  level: "warn",
  service: "memory.dreaming",
  message: "long pause detected: 1.4s without progress on cluster #4",
};

export const EVT_ERROR: LogEvent = {
  id: "evt-error",
  timestamp: "2026-05-05T22:03:09.000Z",
  level: "error",
  service: "cron",
  message: "dreaming.cycle failed: Anthropic API rate-limited",
  traceId: "trace-cron-003",
};

export const EVT_OLD: LogEvent = {
  // fixture 의 max 시각보다 30분 이전 → 5m/15m 범위 필터에서 빠진다.
  id: "evt-old",
  timestamp: "2026-05-05T21:30:00.000Z",
  level: "info",
  service: "system",
  message: "boot sequence complete",
};

export const EVENTS_SAMPLE: readonly LogEvent[] = [
  EVT_INFO,
  EVT_DEBUG,
  EVT_WARN,
  EVT_ERROR,
  EVT_OLD,
];

export const TRACE_SAMPLE: TraceDetail = {
  id: "trace-llm-001",
  name: "llm.router.dispatch",
  startedAt: "2026-05-05T22:00:12.000Z",
  totalMs: 2450,
  status: "success",
  meta: [
    { label: "trace_id", value: "trace-llm-001" },
    { label: "service", value: "llm.router" },
    { label: "duration", value: "2.45s", tone: "info" },
    { label: "status", value: "success", tone: "success" },
  ],
  spans: [
    { id: "s1", name: "router.match", startMs: 0, endMs: 60, tone: "muted" },
    { id: "s2", name: "claude.request", startMs: 65, endMs: 2200, tone: "primary" },
    { id: "s3", name: "claude.stream", startMs: 110, endMs: 2380, tone: "success" },
  ],
  rawJson: {
    trace_id: "trace-llm-001",
    duration_ms: 2450,
  },
};

/** 페이지 단위 테스트가 18개 이상의 행을 만들고 싶을 때 사용. */
export function makeBulkEvents(count: number): LogEvent[] {
  const events: LogEvent[] = [];
  for (let i = 0; i < count; i += 1) {
    events.push({
      id: `bulk-${i}`,
      timestamp: new Date(
        Date.parse("2026-05-05T22:08:01.000Z") - i * 1000,
      ).toISOString(),
      level: "info",
      service: "system",
      message: `bulk message ${i}`,
    });
  }
  return events;
}
