/**
 * Logging & Traces 픽스처 — S11 (BIZ-122) 단계의 mock 데이터.
 *
 * admin.pen `kGAWN` (Logging & Traces Shell) · `EvyYa` (Trace Detail) 시각 spec 에
 * 1:1 매핑한다. 본 단계는 logging 데몬 API 가 아직 미연결이므로 정적 fixture 만
 * 노출한다. 후속 sub-issue (데몬 통합 단계) 가 본 모듈만 비동기로 교체하면
 * 호출부 변경이 없도록, 컴포넌트는 fixture 를 직접 import 하지 않고
 * `getLoggingSnapshot()` 한 함수만 호출한다.
 */
import type { TraceSpan } from "@/design/domain/TraceTimeline";

/** 로그 레벨 — admin.pen `kGAWN` 필터 / `EvyYa` 헤더 라벨의 SSOT. */
export type LogLevel = "debug" | "info" | "warn" | "error";

/** 시간 범위 필터 — 헤더 드롭다운 옵션의 SSOT. */
export type LogTimeRange = "5m" | "15m" | "1h" | "6h" | "24h";

/**
 * 한 줄 로그 이벤트 — 표 한 행과 1:1.
 * `traceId` 가 있으면 행 클릭 시 Trace Detail Modal 이 열린다.
 */
export interface LogEvent {
  /** 영구 키 — slug. */
  id: string;
  /** ISO timestamp. 화면은 hh:mm:ss 까지만 노출. */
  timestamp: string;
  level: LogLevel;
  /** 로그를 발화한 모듈/스킬/데몬. 헤더 service 필터의 SSOT. */
  service: string;
  /** 한 줄 메시지 — 검색 하이라이트 대상. */
  message: string;
  /** trace 연결 — 행 → Trace Detail Modal. */
  traceId?: string;
}

/** Trace Detail 모달의 메타 행 한 건 — 좌측 요약 카드. */
export interface TraceMeta {
  /** 라벨 — "service" / "duration" / "status" 등. */
  label: string;
  /** 값 — 사람이 읽는 짧은 문자열. */
  value: string;
  /** semantic tone — StatusPill / Badge tone 과 정렬. */
  tone?: "neutral" | "success" | "warning" | "error" | "info";
}

/**
 * Trace Detail 모달용 한 trace 의 모든 정보.
 * `EvyYa` 시각 spec — 헤더 / 요약 메타 / TraceTimeline / span 인스펙터 / Raw JSON.
 */
export interface TraceDetail {
  id: string;
  /** 사람이 읽는 trace 이름 — 모달 헤더 큰 글씨. */
  name: string;
  /** 트리거 시각 — 헤더 우상단 보조 라벨. */
  startedAt: string;
  /** 전체 길이 (ms) — TraceTimeline 의 totalMs 와 일치. */
  totalMs: number;
  /** 최상위 status — 헤더 StatusPill 의 SSOT. */
  status: "success" | "failed" | "running";
  /** 좌측 요약 카드의 메타 행. */
  meta: readonly TraceMeta[];
  /** TraceTimeline 가 그릴 span 라인. */
  spans: readonly TraceSpan[];
  /**
   * Raw JSON — 모달 하단 코드 블록.
   * S1 단계는 사람이 읽는 한 객체로만 박제 (실제는 OTel JSON).
   */
  rawJson: Record<string, unknown>;
}

export interface LoggingSnapshot {
  events: readonly LogEvent[];
  traces: readonly TraceDetail[];
}

/** 페이지 헤더 시간 범위 드롭다운의 옵션 SSOT. */
export const TIME_RANGE_OPTIONS: ReadonlyArray<{
  value: LogTimeRange;
  label: string;
}> = [
  { value: "5m", label: "최근 5분" },
  { value: "15m", label: "최근 15분" },
  { value: "1h", label: "최근 1시간" },
  { value: "6h", label: "최근 6시간" },
  { value: "24h", label: "최근 24시간" },
];

/** 페이지 헤더 레벨 드롭다운의 옵션 SSOT — "all" 포함. */
export const LEVEL_OPTIONS: ReadonlyArray<{
  value: LogLevel | "all";
  label: string;
}> = [
  { value: "all", label: "전체 레벨" },
  { value: "debug", label: "DEBUG" },
  { value: "info", label: "INFO" },
  { value: "warn", label: "WARN" },
  { value: "error", label: "ERROR" },
];

/** 시간 범위 → 분 단위 환산. 필터 helper 가 사용. */
export function timeRangeMinutes(range: LogTimeRange): number {
  switch (range) {
    case "5m":
      return 5;
    case "15m":
      return 15;
    case "1h":
      return 60;
    case "6h":
      return 60 * 6;
    case "24h":
      return 60 * 24;
  }
}

/** Logging 화면이 그릴 모든 데이터를 한 번에 반환. */
export function getLoggingSnapshot(): LoggingSnapshot {
  return {
    events: EVENTS,
    traces: TRACES,
  };
}

/** events 의 distinct service 목록 — 헤더 service 드롭다운 SSOT. */
export function listServices(events: readonly LogEvent[]): string[] {
  const set = new Set<string>();
  for (const e of events) set.add(e.service);
  return Array.from(set).sort();
}

/** traceId 로 TraceDetail 단건 조회 — 모달 오픈 시 사용. */
export function findTraceById(
  traces: readonly TraceDetail[],
  id: string,
): TraceDetail | null {
  return traces.find((t) => t.id === id) ?? null;
}

export interface LogFilter {
  query: string;
  level: LogLevel | "all";
  service: string | "all";
  range: LogTimeRange;
  /**
   * "지금" 의 기준 시각. 테스트가 결정적으로 동작하도록 외부에서 주입 가능.
   * 미지정 시 마지막 이벤트의 timestamp 를 기준으로 — fixture 가 고정 시각이라
   * `Date.now()` 에 의존하면 테스트가 시간 경과에 따라 깨지기 때문.
   */
  now?: number;
}

/**
 * 4개 필터를 한 번에 적용 — page 와 단위 테스트가 공유.
 *
 * 정렬은 timestamp 내림차순 (최신 먼저) — 표의 시각적 SSOT.
 */
export function applyLogFilter(
  events: readonly LogEvent[],
  filter: LogFilter,
): LogEvent[] {
  const q = filter.query.trim().toLowerCase();
  const nowMs =
    filter.now ??
    (events.length > 0
      ? Math.max(...events.map((e) => Date.parse(e.timestamp)))
      : Date.now());
  const cutoff = nowMs - timeRangeMinutes(filter.range) * 60_000;
  const filtered = events.filter((e) => {
    if (filter.level !== "all" && e.level !== filter.level) return false;
    if (filter.service !== "all" && e.service !== filter.service) return false;
    if (Date.parse(e.timestamp) < cutoff) return false;
    if (q.length > 0) {
      const inMessage = e.message.toLowerCase().includes(q);
      const inService = e.service.toLowerCase().includes(q);
      if (!inMessage && !inService) return false;
    }
    return true;
  });
  filtered.sort(
    (a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp),
  );
  return filtered;
}

/**
 * 검색 하이라이트 — 메시지 중 query 와 일치하는 substring 을 `<mark>` 로 감싼다.
 * React 가 안전하게 렌더할 수 있도록 토큰 배열을 반환한다.
 */
export interface HighlightChunk {
  text: string;
  match: boolean;
}

export function highlightMatches(
  text: string,
  query: string,
): HighlightChunk[] {
  const q = query.trim();
  if (!q) return [{ text, match: false }];
  const lower = text.toLowerCase();
  const needle = q.toLowerCase();
  const chunks: HighlightChunk[] = [];
  let cursor = 0;
  while (cursor < text.length) {
    const idx = lower.indexOf(needle, cursor);
    if (idx === -1) {
      chunks.push({ text: text.slice(cursor), match: false });
      break;
    }
    if (idx > cursor) {
      chunks.push({ text: text.slice(cursor, idx), match: false });
    }
    chunks.push({ text: text.slice(idx, idx + needle.length), match: true });
    cursor = idx + needle.length;
  }
  return chunks;
}

// ────────────────────────────────────────────────────────────────────────────
// Fixtures — 17 events / 5 traces.
// admin.pen `kGAWN` 시각 spec 의 실제 데이터 라인을 그대로 박제.
// ────────────────────────────────────────────────────────────────────────────

const TRACES: readonly TraceDetail[] = [
  {
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
      { label: "spans", value: "4" },
    ],
    spans: [
      { id: "s1", name: "router.match", startMs: 0, endMs: 60, tone: "muted" },
      { id: "s2", name: "claude.request", startMs: 65, endMs: 2200, tone: "primary" },
      { id: "s3", name: "claude.stream", startMs: 110, endMs: 2380, tone: "success" },
      { id: "s4", name: "router.commit", startMs: 2390, endMs: 2450, tone: "muted" },
    ],
    rawJson: {
      trace_id: "trace-llm-001",
      service: "llm.router",
      span_count: 4,
      duration_ms: 2450,
      status: "ok",
      attributes: {
        "llm.model": "claude-opus-4-6",
        "llm.tokens.in": 1820,
        "llm.tokens.out": 412,
      },
    },
  },
  {
    id: "trace-skill-002",
    name: "skill.gmail.search",
    startedAt: "2026-05-05T22:01:48.000Z",
    totalMs: 1820,
    status: "success",
    meta: [
      { label: "trace_id", value: "trace-skill-002" },
      { label: "service", value: "skills" },
      { label: "duration", value: "1.82s", tone: "info" },
      { label: "status", value: "success", tone: "success" },
      { label: "spans", value: "3" },
    ],
    spans: [
      { id: "s1", name: "skill.load", startMs: 0, endMs: 120, tone: "muted" },
      { id: "s2", name: "gmail.query", startMs: 130, endMs: 1700, tone: "primary" },
      { id: "s3", name: "skill.persist", startMs: 1710, endMs: 1820, tone: "success" },
    ],
    rawJson: {
      trace_id: "trace-skill-002",
      service: "skills",
      span_count: 3,
      duration_ms: 1820,
      status: "ok",
      attributes: {
        "skill.id": "gmail-skill",
        "skill.invocation": "search",
        "result.count": 12,
      },
    },
  },
  {
    id: "trace-cron-003",
    name: "cron.dreaming.cycle",
    startedAt: "2026-05-05T22:03:05.000Z",
    totalMs: 4200,
    status: "failed",
    meta: [
      { label: "trace_id", value: "trace-cron-003" },
      { label: "service", value: "cron" },
      { label: "duration", value: "4.20s", tone: "info" },
      { label: "status", value: "failed", tone: "error" },
      { label: "spans", value: "5" },
    ],
    spans: [
      { id: "s1", name: "cron.tick", startMs: 0, endMs: 50, tone: "muted" },
      { id: "s2", name: "memory.scan", startMs: 60, endMs: 1900, tone: "primary" },
      { id: "s3", name: "llm.summarize", startMs: 1910, endMs: 3600, tone: "primary" },
      { id: "s4", name: "memory.persist", startMs: 3610, endMs: 4100, tone: "warning" },
      { id: "s5", name: "cron.commit", startMs: 4110, endMs: 4200, tone: "error" },
    ],
    rawJson: {
      trace_id: "trace-cron-003",
      service: "cron",
      span_count: 5,
      duration_ms: 4200,
      status: "error",
      error: {
        type: "rate-limit",
        message: "Anthropic API rate-limited at span llm.summarize",
      },
    },
  },
  {
    id: "trace-channel-004",
    name: "channel.telegram.dispatch",
    startedAt: "2026-05-05T22:04:21.000Z",
    totalMs: 980,
    status: "success",
    meta: [
      { label: "trace_id", value: "trace-channel-004" },
      { label: "service", value: "channels.telegram" },
      { label: "duration", value: "0.98s", tone: "info" },
      { label: "status", value: "success", tone: "success" },
      { label: "spans", value: "3" },
    ],
    spans: [
      { id: "s1", name: "webhook.receive", startMs: 0, endMs: 80, tone: "muted" },
      { id: "s2", name: "agent.handle", startMs: 90, endMs: 850, tone: "primary" },
      { id: "s3", name: "telegram.reply", startMs: 860, endMs: 980, tone: "success" },
    ],
    rawJson: {
      trace_id: "trace-channel-004",
      service: "channels.telegram",
      span_count: 3,
      duration_ms: 980,
      status: "ok",
      attributes: {
        "telegram.chat_id": "•••••42",
        "telegram.update_id": 8821,
      },
    },
  },
  {
    id: "trace-secret-005",
    name: "secrets.rotate.openai",
    startedAt: "2026-05-05T22:05:55.000Z",
    totalMs: 1320,
    status: "success",
    meta: [
      { label: "trace_id", value: "trace-secret-005" },
      { label: "service", value: "secrets" },
      { label: "duration", value: "1.32s", tone: "info" },
      { label: "status", value: "success", tone: "success" },
      { label: "spans", value: "4" },
    ],
    spans: [
      { id: "s1", name: "secrets.lock", startMs: 0, endMs: 60, tone: "muted" },
      { id: "s2", name: "vault.write", startMs: 70, endMs: 700, tone: "primary" },
      { id: "s3", name: "audit.append", startMs: 710, endMs: 1180, tone: "success" },
      { id: "s4", name: "secrets.unlock", startMs: 1200, endMs: 1320, tone: "muted" },
    ],
    rawJson: {
      trace_id: "trace-secret-005",
      service: "secrets",
      span_count: 4,
      duration_ms: 1320,
      status: "ok",
      attributes: {
        "secret.id": "openai_api_key",
        "audit.actor": "operator",
      },
    },
  },
];

const EVENTS: readonly LogEvent[] = [
  {
    id: "evt-1",
    timestamp: "2026-05-05T22:00:12.000Z",
    level: "info",
    service: "llm.router",
    message: "dispatched claude-opus-4-6 (1820 in / 412 out)",
    traceId: "trace-llm-001",
  },
  {
    id: "evt-2",
    timestamp: "2026-05-05T22:00:13.000Z",
    level: "debug",
    service: "llm.router",
    message: "fallback chain primed: claude → openai → gemini",
  },
  {
    id: "evt-3",
    timestamp: "2026-05-05T22:01:01.000Z",
    level: "info",
    service: "agent",
    message: "tool_call: gmail-skill.search query='invoice'",
  },
  {
    id: "evt-4",
    timestamp: "2026-05-05T22:01:48.000Z",
    level: "info",
    service: "skills",
    message: "skill gmail-skill returned 12 messages in 1820ms",
    traceId: "trace-skill-002",
  },
  {
    id: "evt-5",
    timestamp: "2026-05-05T22:02:30.000Z",
    level: "warn",
    service: "memory.dreaming",
    message: "long pause detected: 1.4s without progress on cluster #4",
  },
  {
    id: "evt-6",
    timestamp: "2026-05-05T22:03:05.000Z",
    level: "info",
    service: "cron",
    message: "cron tick: dreaming.cycle (every 2h)",
    traceId: "trace-cron-003",
  },
  {
    id: "evt-7",
    timestamp: "2026-05-05T22:03:09.000Z",
    level: "error",
    service: "cron",
    message: "dreaming.cycle failed: Anthropic API rate-limited",
    traceId: "trace-cron-003",
  },
  {
    id: "evt-8",
    timestamp: "2026-05-05T22:03:09.500Z",
    level: "warn",
    service: "cron",
    message: "circuit breaker → open for dreaming.cycle (3 retries exhausted)",
  },
  {
    id: "evt-9",
    timestamp: "2026-05-05T22:04:00.000Z",
    level: "info",
    service: "channels.telegram",
    message: "webhook accepted update_id=8821",
  },
  {
    id: "evt-10",
    timestamp: "2026-05-05T22:04:21.000Z",
    level: "info",
    service: "channels.telegram",
    message: "telegram reply sent in 980ms",
    traceId: "trace-channel-004",
  },
  {
    id: "evt-11",
    timestamp: "2026-05-05T22:04:55.000Z",
    level: "debug",
    service: "agent",
    message: "persona resolved AGENT.md (4123 tokens) + USER.md (820 tokens)",
  },
  {
    id: "evt-12",
    timestamp: "2026-05-05T22:05:10.000Z",
    level: "info",
    service: "secrets",
    message: "rotation requested for openai_api_key (operator)",
  },
  {
    id: "evt-13",
    timestamp: "2026-05-05T22:05:55.000Z",
    level: "info",
    service: "secrets",
    message: "rotation completed for openai_api_key in 1320ms",
    traceId: "trace-secret-005",
  },
  {
    id: "evt-14",
    timestamp: "2026-05-05T22:06:02.000Z",
    level: "warn",
    service: "channels.webhook",
    message: "rate-limit warning: 28/30 req/min from 127.0.0.1",
  },
  {
    id: "evt-15",
    timestamp: "2026-05-05T22:06:40.000Z",
    level: "error",
    service: "channels.webhook",
    message: "401 unauthorized: missing webhook token",
  },
  {
    id: "evt-16",
    timestamp: "2026-05-05T22:07:20.000Z",
    level: "info",
    service: "memory.dreaming",
    message: "cluster #4 reflection completed in 4.2s",
  },
  {
    id: "evt-17",
    timestamp: "2026-05-05T22:08:01.000Z",
    level: "debug",
    service: "system",
    message: "heartbeat ok — uptime 4d 12h, rss 132MB",
  },
];
