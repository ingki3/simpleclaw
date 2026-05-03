/**
 * LLM 라우터 영역의 Admin API 래퍼 — ``/admin/v1/config/llm``,
 * ``/admin/v1/secrets/...``를 타입화해 노출한다.
 *
 * 본 파일의 함수들은 모두 ``fetchAdmin``을 거치므로 인증/에러 처리는 거기서
 * 일원화된다. 응답 dict의 키 이름은 admin_api 핸들러의 반환 형태와 1:1로 맞춘다.
 */

import { fetchAdmin } from "./admin";

// ---------------------------------------------------------------------------
// 타입 — config.yaml의 ``llm`` 섹션 + Admin UI(BIZ-45)에서 도입한 ``routing``.
// ---------------------------------------------------------------------------

export type ProviderType = "api" | "cli";

export interface ProviderConfig {
  type?: ProviderType;
  /** 모델 식별자 — 프로바이더별 ID. */
  model?: string;
  /** 시크릿 참조 문자열(``keyring:claude_api_key`` 등) 또는 마스킹된 평문. */
  api_key?: string;
  /** 프로바이더별 토큰 예산 — admin-requirements.md §1 1번. 미정의 시 무제한. */
  token_budget?: number;
  /** 폴백 우선순위 — 정수. 작을수록 우선. */
  fallback_priority?: number;
  /** Admin UI 전용 메타: 활성/비활성 토글. type=api와 별도로 운영자 의도를 표현. */
  enabled?: boolean;
  /** 그 외 키는 그대로 보존(미래 확장). */
  [k: string]: unknown;
}

/** 카테고리별 라우팅 — Admin UI(BIZ-45)가 도입. 값은 provider 이름. */
export type RoutingMap = Partial<Record<RoutingCategory, string>>;

export type RoutingCategory = "general" | "coding" | "reasoning" | "tools";

export const ROUTING_CATEGORIES: readonly RoutingCategory[] = [
  "general",
  "coding",
  "reasoning",
  "tools",
] as const;

export interface LLMConfig {
  default?: string;
  providers?: Record<string, ProviderConfig>;
  routing?: RoutingMap;
}

/** ``classify_keys``의 응답 — UI에서 ``Hot/Service-restart/Process-restart`` 라벨로 변환. */
export interface PolicyResultDTO {
  level: "Hot" | "Service-restart" | "Process-restart";
  requires_restart: boolean;
  affected_modules: string[];
  matched_keys: string[];
}

/** dry-run PATCH 응답. */
export interface DryRunResponse {
  outcome: "dry_run";
  diff: { before: unknown; after: unknown };
  policy: PolicyResultDTO;
}

/** apply 성공 PATCH 응답. */
export interface ApplyResponse {
  outcome: "applied" | "pending";
  audit_id: string;
  policy?: PolicyResultDTO;
  message?: string;
}

export interface SecretMeta {
  name: string;
  backend: "env" | "keyring" | "file" | string;
  last_rotated_at: string | null;
}

export interface RevealResponse {
  name: string;
  backend: string;
  value: string;
  nonce: string;
  expires_in_seconds: number;
}

// ---------------------------------------------------------------------------
// 호출
// ---------------------------------------------------------------------------

/** 현재 ``llm`` 영역 설정을 반환한다 — 시크릿은 마스킹된 참조 문자열로 노출. */
export async function getLLMConfig(): Promise<LLMConfig> {
  const data = await fetchAdmin<{ area: string; config: LLMConfig }>(
    "/admin/v1/config/llm",
  );
  return data.config ?? {};
}

/** dry-run PATCH — yaml/볼트는 건드리지 않고 검증·정책·diff만 받는다. */
export function dryRunLLMPatch(patch: Partial<LLMConfig>): Promise<DryRunResponse> {
  return fetchAdmin<DryRunResponse>("/admin/v1/config/llm", {
    method: "PATCH",
    json: patch,
    dryRun: true,
  });
}

/** 실제 적용 PATCH — Hot은 즉시, Service-restart는 yaml 반영 + 재기동 트리거. */
export function applyLLMPatch(patch: Partial<LLMConfig>): Promise<ApplyResponse> {
  return fetchAdmin<ApplyResponse>("/admin/v1/config/llm", {
    method: "PATCH",
    json: patch,
  });
}

/** 시크릿 메타데이터(이름·백엔드·마지막 회전 시각). */
export async function listSecrets(): Promise<SecretMeta[]> {
  const data = await fetchAdmin<{ secrets: SecretMeta[] }>(
    "/admin/v1/secrets",
  );
  return data.secrets ?? [];
}

/** 시크릿 평문을 일회성으로 노출 — 호출 측에서 5초 후 마스킹. */
export function revealSecret(
  name: string,
  backend?: string,
): Promise<RevealResponse> {
  return fetchAdmin<RevealResponse>(`/admin/v1/secrets/${encodeURIComponent(name)}/reveal`, {
    method: "POST",
    query: backend ? { backend } : undefined,
    json: {},
  });
}

/** 시크릿을 새 값으로 회전. */
export function rotateSecret(
  name: string,
  value: string,
  backend?: string,
): Promise<{ outcome: string; audit_id: string; backend: string; name: string }> {
  return fetchAdmin(`/admin/v1/secrets/${encodeURIComponent(name)}/rotate`, {
    method: "POST",
    json: { value, backend },
  });
}

/** 감사 로그 항목 되돌리기 — 5분 undo 토스트의 백엔드. */
export function undoAudit(id: string): Promise<{ outcome: string; audit_id: string }> {
  return fetchAdmin(`/admin/v1/audit/${encodeURIComponent(id)}/undo`, {
    method: "POST",
    json: {},
  });
}
