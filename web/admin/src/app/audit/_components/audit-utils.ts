/**
 * Audit 화면용 도메인 유틸 — 시간 포맷, 결과 톤 매핑, undo 윈도 판정.
 *
 * 본 모듈은 React 의존이 없는 순수 함수만 모은다(테스트 용이성). 화면 컴포넌트는
 * 본 유틸이 정한 규칙을 따라 표시·활성화 여부를 결정한다.
 *
 * 정책 결정:
 *  - undo 윈도는 5분 — admin-requirements §4(감사·롤백) / DESIGN.md §1 #6.
 *    감사 항목 자체가 ``undoable=true``여도 5분이 지나면 비활성화된다.
 *  - 시크릿 회전·시스템 재시작·`secret.reveal` 등은 백엔드가 ``undoable=false``로
 *    기록하므로 본 모듈은 추가 분기를 두지 않는다(데이터 신뢰).
 */

import type { StatusTone } from "@/components/atoms/StatusPill";

/** 5분(밀리초). */
export const UNDO_WINDOW_MS = 5 * 60 * 1000;

/**
 * 백엔드 ``GET /admin/v1/audit`` 응답의 단일 항목 형태.
 *
 * ``src/simpleclaw/channels/admin_audit.py``의 ``AuditEntry``와 1:1.
 * before/after는 백엔드가 시크릿 패턴을 자동 마스킹한 *부분 트리*다. 본 화면은
 * 추가 마스킹 없이 그대로 노출하되, 시크릿 키 옆 값은 항상 ``••••<last4>``
 * 또는 ``(redacted)`` 형태로 표시되도록 백엔드 계약을 신뢰한다.
 */
export interface AuditEntryDTO {
  id: string;
  ts: string;
  actor_id: string;
  trace_id: string;
  action: string;
  area: string;
  target: string;
  before?: unknown;
  after?: unknown;
  outcome: "applied" | "pending" | "dry_run" | "rejected" | string;
  requires_restart: boolean;
  affected_modules: string[];
  undoable: boolean;
  reason?: string | null;
}

/** 결과 라벨에 매핑되는 시각 톤. 미지정 액션은 neutral로 폴백한다. */
export const OUTCOME_TONE: Record<string, StatusTone> = {
  applied: "success",
  pending: "info",
  dry_run: "neutral",
  rejected: "warning",
  failed: "error",
  rolled_back: "neutral",
};

export function outcomeTone(outcome: string): StatusTone {
  return OUTCOME_TONE[outcome] ?? "neutral";
}

/**
 * 주어진 항목이 *지금* 되돌릴 수 있는지 판단한다.
 *
 *  - 백엔드가 ``undoable=true``로 표시했고
 *  - 결과가 ``applied`` 또는 ``pending`` 이며
 *  - 기록 시각으로부터 ``UNDO_WINDOW_MS`` 이내인 경우만 true.
 *
 * 시각 비교는 호출 측이 주입한 ``now``를 기준으로 — 테스트에서 deterministic.
 */
export function isUndoableNow(
  entry: Pick<AuditEntryDTO, "undoable" | "outcome" | "ts">,
  now: number = Date.now(),
): boolean {
  if (!entry.undoable) return false;
  if (entry.outcome !== "applied" && entry.outcome !== "pending") return false;
  const ts = parseTs(entry.ts);
  if (ts === null) return false;
  return now - ts <= UNDO_WINDOW_MS;
}

/** ISO 또는 epoch(s/ms) 형태의 ts를 epoch ms로 변환. 실패하면 null. */
export function parseTs(ts: string | number | null | undefined): number | null {
  if (ts === null || ts === undefined) return null;
  if (typeof ts === "number") return ts < 1e12 ? ts * 1000 : ts;
  const n = Date.parse(ts);
  return Number.isFinite(n) ? n : null;
}

/**
 * 사람이 읽기 쉬운 상대 시간 — 1분 미만은 "방금", 1시간 미만은 "N분 전" 등.
 *
 * 절대 시각(상세 Drawer)에서는 ``formatAbsoluteTs``를 사용한다.
 */
export function formatRelativeTs(ts: string | number | undefined, now: number = Date.now()): string {
  const ms = parseTs(ts ?? null);
  if (ms === null) return ts === undefined ? "" : String(ts);
  const diff = now - ms;
  if (diff < 60_000) return "방금";
  if (diff < 3_600_000) return `${Math.round(diff / 60_000)}분 전`;
  if (diff < 86_400_000) return `${Math.round(diff / 3_600_000)}시간 전`;
  return new Date(ms).toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatAbsoluteTs(ts: string | number | undefined): string {
  const ms = parseTs(ts ?? null);
  if (ms === null) return ts === undefined ? "" : String(ts);
  return new Date(ms).toLocaleString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/**
 * undo 만료까지 남은 ``mm:ss`` — 윈도우 밖이면 "—".
 *
 * 토스트의 카운트다운과 동일 포맷을 유지해 시각 일관성을 갖는다.
 */
export function remainingWindowLabel(
  entry: Pick<AuditEntryDTO, "undoable" | "outcome" | "ts">,
  now: number = Date.now(),
): string {
  if (!isUndoableNow(entry, now)) return "—";
  const ts = parseTs(entry.ts) ?? now;
  const remainSec = Math.max(0, Math.ceil((ts + UNDO_WINDOW_MS - now) / 1000));
  const m = Math.floor(remainSec / 60);
  const s = remainSec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/**
 * before/after 페이로드를 사람이 읽기 쉬운 multi-line JSON 문자열로 변환한다.
 *
 *  - dict/list는 2-space indent JSON.
 *  - 원시 string은 그대로(따옴표 없이).
 *  - undefined/null은 "—".
 *
 * 시크릿 마스킹은 백엔드에서 끝났음을 신뢰한다 (``_mask_secrets``).
 */
export function formatPayload(value: unknown): string {
  if (value === undefined || value === null) return "—";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/** 한 줄 요약 — AuditRow에 배치되는 압축 표현. */
export function formatPayloadInline(value: unknown): string | undefined {
  if (value === undefined || value === null) return undefined;
  if (typeof value === "string") return value;
  try {
    const s = JSON.stringify(value);
    return s.length > 80 ? s.slice(0, 77) + "…" : s;
  } catch {
    return String(value);
  }
}

/**
 * 백엔드 영역 키 — admin_api의 ``AREA_TO_YAML_KEY`` 키 + ``secrets``/``audit``/``daemon``.
 *
 * 필터 드롭다운에 채울 항목. 운영 중 새 area가 등장해도 *목록에 없을 뿐* 검색
 * 자체는 가능하다(서버는 임의 area 문자열을 그대로 통과시킨다).
 */
export const AUDIT_AREAS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "llm", label: "LLM" },
  { value: "agent", label: "Agent" },
  { value: "memory", label: "Memory" },
  { value: "security", label: "Security" },
  { value: "skills", label: "Skills" },
  { value: "mcp", label: "MCP" },
  { value: "voice", label: "Voice" },
  { value: "telegram", label: "Telegram" },
  { value: "webhook", label: "Webhook" },
  { value: "channels", label: "Channels" },
  { value: "sub_agents", label: "Sub-agents" },
  { value: "daemon", label: "Daemon" },
  { value: "cron", label: "Cron" },
  { value: "persona", label: "Persona" },
  { value: "system", label: "System" },
  { value: "secrets", label: "Secrets" },
];

/** 액션 필터 — DESIGN.md / admin-requirements가 정의한 표준 액션 라벨. */
export const AUDIT_ACTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "config.update", label: "config.update" },
  { value: "config.create", label: "config.create" },
  { value: "config.delete", label: "config.delete" },
  { value: "secret.rotate", label: "secret.rotate" },
  { value: "secret.reveal", label: "secret.reveal" },
  { value: "secret.reencrypt", label: "secret.reencrypt" },
  { value: "system.restart", label: "system.restart" },
];
