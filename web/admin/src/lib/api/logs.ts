/**
 * Admin Logs 데이터 계층 — `/admin/v1/logs` 엔드포인트 호출 헬퍼와 타입.
 *
 * 본 모듈은 백엔드 응답 envelope을 페이지가 곧바로 렌더할 수 있는 형태로 정규화한다.
 * 백엔드는 다음을 보장한다(``src/simpleclaw/channels/admin_api.py::_handle_search_logs``):
 *   - 응답: ``{ entries: LogApiEntry[] }`` — 시간순 오름차순(파일 append 순서).
 *   - 쿼리: ``trace_id`` / ``limit`` / ``level`` / ``module``.
 *   - ``level``은 대소문자 strict 비교다 → 페이지에서 사용하는 사용자 친화적 소문자
 *     레이블(``warn`` 등)을 백엔드가 받는 정식 값(``WARNING``)으로 매핑해 보낸다.
 *   - ``module``은 ``action_type``에 부분 포함되는지 검사 — 자유 입력 substring.
 *
 * 검색(자유 텍스트)은 백엔드가 지원하지 않으므로 클라이언트 측 substring 매칭으로
 * 보강한다 — 본 페이지가 다루는 1만+ 항목 규모에서도 충분히 빠르다.
 */

export type LogLevel = "DEBUG" | "INFO" | "WARNING" | "ERROR";

/** UI 라벨(소문자) — URL · 필터 토큰. */
export type LogLevelToken = "debug" | "info" | "warn" | "error";

export const LEVEL_TOKEN_TO_API: Record<LogLevelToken, LogLevel> = {
  debug: "DEBUG",
  info: "INFO",
  warn: "WARNING",
  error: "ERROR",
};

export const LEVEL_API_TO_TOKEN: Record<LogLevel, LogLevelToken> = {
  DEBUG: "debug",
  INFO: "info",
  WARNING: "warn",
  ERROR: "error",
};

export const LEVEL_TOKENS: readonly LogLevelToken[] = [
  "debug",
  "info",
  "warn",
  "error",
] as const;

/** 백엔드 응답 한 항목 — ``LogEntry`` dataclass의 dict 직렬화. */
export interface LogApiEntry {
  timestamp?: string;
  level?: string;
  action_type?: string;
  input_summary?: string;
  output_summary?: string;
  duration_ms?: number;
  status?: string;
  trace_id?: string;
  details?: Record<string, unknown>;
  /** 미래 필드를 보존 — UI는 알지 못하는 키도 JSON 원문에서 보여줄 수 있어야 한다. */
  [k: string]: unknown;
}

export interface LogsResponse {
  entries: LogApiEntry[];
}

export interface LogsQuery {
  /** 백엔드로 보낼 최대 항목 수. */
  limit: number;
  /** 단일 trace_id로 필터(검색 무관). */
  traceId?: string;
  /** UI 토큰(``warn`` 등) — 백엔드 ``WARNING``으로 매핑. */
  level?: LogLevelToken;
  /** ``action_type`` substring. */
  module?: string;
}

/**
 * 쿼리 객체를 ``/admin/v1/logs?...`` 경로 문자열로 변환한다.
 *
 * SWR 캐시 키와 useAdminResource 의 인자로 동시에 쓰이므로 안정적으로 정렬된
 * 키 순서를 갖도록 ``URLSearchParams``를 통과시킨다.
 */
export function buildLogsPath(q: LogsQuery): string {
  const params = new URLSearchParams();
  params.set("limit", String(q.limit));
  if (q.traceId) params.set("trace_id", q.traceId);
  if (q.level) params.set("level", LEVEL_TOKEN_TO_API[q.level]);
  if (q.module) params.set("module", q.module);
  return `/admin/v1/logs?${params.toString()}`;
}

/**
 * 클라이언트 측 자유 검색 — 백엔드가 미지원이라 화면에서 별도 필터.
 *
 * 매칭 대상: ``action_type``, ``input_summary``, ``output_summary``, ``status``,
 * ``trace_id``, ``details`` JSON 직렬화 결과. 비교는 case-insensitive substring.
 */
export function entryMatchesSearch(
  entry: LogApiEntry,
  needle: string,
): boolean {
  if (!needle) return true;
  const lower = needle.toLowerCase();
  const haystacks: (string | undefined)[] = [
    entry.action_type,
    entry.input_summary,
    entry.output_summary,
    entry.status,
    entry.trace_id,
  ];
  for (const h of haystacks) {
    if (h && h.toLowerCase().includes(lower)) return true;
  }
  if (entry.details && typeof entry.details === "object") {
    try {
      if (JSON.stringify(entry.details).toLowerCase().includes(lower))
        return true;
    } catch {
      // 순환 참조 등은 무시.
    }
  }
  return false;
}

/**
 * 항목의 안정 키 — 같은 timestamp + trace_id + action_type 조합이면 동일 항목으로
 * 간주한다. fade-in 검출/diff 추적에 사용.
 */
export function entryKey(entry: LogApiEntry, fallbackIndex = 0): string {
  const ts = entry.timestamp ?? "";
  const tid = entry.trace_id ?? "";
  const at = entry.action_type ?? "";
  return ts && (tid || at) ? `${ts}|${tid}|${at}` : `idx-${fallbackIndex}`;
}

/** ``"INFO"`` / ``"info"`` 같은 다양한 입력을 정규화. */
export function normalizeLevel(raw: string | undefined): LogLevel | undefined {
  if (!raw) return undefined;
  const upper = raw.toUpperCase();
  if (upper === "WARN") return "WARNING";
  if (upper === "DEBUG" || upper === "INFO" || upper === "WARNING" || upper === "ERROR") {
    return upper;
  }
  return undefined;
}

/** ``"info"``/``"warn"`` URL 토큰 검증 — 잘못된 값이면 undefined. */
export function parseLevelToken(raw: string | null | undefined): LogLevelToken | undefined {
  if (!raw) return undefined;
  const lower = raw.toLowerCase();
  return (LEVEL_TOKENS as readonly string[]).includes(lower)
    ? (lower as LogLevelToken)
    : undefined;
}
