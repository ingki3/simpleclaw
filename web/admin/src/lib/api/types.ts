/**
 * Admin API 응답/요청 타입 정의 — BIZ-41 백엔드 스키마와 1:1 대응.
 *
 * 백엔드 핸들러는 ``src/simpleclaw/channels/admin_api.py``에 정의돼 있으며,
 * 본 파일은 *클라이언트가 본 그대로의 모양*만 담는다. 추가 가공/정규화는
 * 영역별 화면에서 수행한다.
 */

/** 정책 분류 — admin_policy.py와 동일 라벨. */
export type PolicyLevel = "hot" | "service_restart" | "process_restart";

/** PATCH/PATCH?dry_run=true 응답에 동봉되는 정책 메타. */
export interface PolicySummary {
  level: PolicyLevel;
  requires_restart: boolean;
  affected_modules: string[];
  /** Hot/Service-restart/Process-restart 키 분류(사람이 읽는 라벨). */
  hot?: string[];
  service_restart?: string[];
  process_restart?: string[];
}

/** Dry-run 모드에서만 채워지는 diff 페이로드. */
export interface DryRunDiff {
  before: unknown;
  after: unknown;
}

/** PATCH 응답 — outcome에 따라 dry-run/pending/applied 분기. */
export interface ConfigPatchResponse {
  outcome: "dry_run" | "pending" | "applied";
  audit_id?: string;
  policy: PolicySummary;
  diff?: DryRunDiff;
  message?: string;
}

/** 422 검증 실패 응답. */
export interface ValidationErrorBody {
  error: string;
  errors?: string[];
}

/** 단일 시크릿 메타. 값은 절대 포함되지 않는다. */
export interface SecretMeta {
  name: string;
  backend: "env" | "keyring" | "file";
  last_rotated_at: string | null;
}

export interface ListSecretsResponse {
  items: SecretMeta[];
}

/** 감사 항목 — admin_audit.py 직렬화 형태. */
export interface AuditEntryDTO {
  id: string;
  ts: string;
  actor_id: string;
  action: string;
  area: string;
  target: string;
  outcome: "applied" | "rejected" | "pending" | "dry_run";
  before: unknown;
  after: unknown;
  requires_restart: boolean;
  affected_modules: string[];
  undoable: boolean;
  reason?: string;
  trace_id?: string;
}

export interface SearchAuditResponse {
  entries: AuditEntryDTO[];
}

export interface UndoAuditResponse {
  outcome: "applied";
  audit_id: string;
}

/** 헬스 스냅샷 — 영역별 상태가 어떤 키로 들어올지는 daemon 측 확장에 따라 다르다. */
export interface HealthSnapshot {
  status: "ok" | "warn" | "error";
  uptime_seconds: number;
  pending_changes: boolean;
  metrics?: Record<string, number>;
  /** daemon health_provider가 추가하는 자유형 필드. */
  [key: string]: unknown;
}

/** 시스템 재시작 응답. */
export interface SystemRestartResponse {
  outcome: "applied";
  audit_id: string;
  applied_pending: number;
}

/** Admin UI 영역 — DESIGN.md §3.3 사이드바 11개 항목과는 다소 다르며, 백엔드의
 *  ``AREA_TO_YAML_KEY`` 키 집합과 일치한다. */
export type AdminArea =
  | "llm"
  | "agent"
  | "memory"
  | "security"
  | "skills"
  | "mcp"
  | "voice"
  | "telegram"
  | "webhook"
  | "channels"
  | "sub_agents"
  | "daemon"
  | "cron"
  | "persona"
  | "system";
