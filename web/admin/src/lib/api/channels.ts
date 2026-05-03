/**
 * Channels API 클라이언트 — Telegram/Webhook 영역의 ``/admin/v1/*`` 호출.
 *
 * 백엔드 admin_api.py의 다음 엔드포인트를 타입화해 노출한다:
 *  - ``GET    /admin/v1/config/channels``       — telegram + webhook 묶음 조회
 *  - ``PATCH  /admin/v1/config/telegram``       — 텔레그램 부분 갱신 (dry-run 지원)
 *  - ``PATCH  /admin/v1/config/webhook``        — 웹훅 부분 갱신 (dry-run 지원)
 *  - ``POST   /admin/v1/channels/{name}/test``  — 테스트 발송 ("Hello from admin")
 *  - ``GET    /admin/v1/secrets``               — 시크릿 메타 (백엔드 라벨/마지막 회전)
 *  - ``POST   /admin/v1/secrets/{name}/reveal`` — 일회성 평문 (5초 마스킹 정책 권장)
 *  - ``POST   /admin/v1/secrets/{name}/rotate`` — 회전
 *  - ``POST   /admin/v1/audit/{id}/undo``       — 변경 되돌리기 (5분 윈도)
 *
 * 본 모듈은 BIZ-43에서 도입된 ``admin.ts``의 ``fetchAdmin`` 헬퍼를 그대로 사용하며,
 * 새 ``@/lib/api`` 배럴이 안정화되면 그쪽으로 마이그레이션할 수 있도록 시그니처를
 * 단순하게 유지한다.
 */

import { fetchAdmin } from "./admin";

// ---------------------------------------------------------------------------
// 설정 타입 — config.yaml의 telegram/webhook 섹션과 1:1 매핑.
// ---------------------------------------------------------------------------

export interface TelegramWhitelist {
  user_ids?: number[];
  chat_ids?: number[];
}

export interface TelegramConfig {
  /** 시크릿 참조(``keyring:telegram_bot_token`` 등) 또는 평문/마스킹된 값. */
  bot_token?: string;
  whitelist?: TelegramWhitelist;
  [k: string]: unknown;
}

export interface WebhookConfig {
  /** 채널 활성화. ``false``면 서버 부팅 시 웹훅 라우터를 띄우지 않는다. */
  enabled?: boolean;
  host?: string;
  port?: number;
  /** 시크릿 참조 또는 마스킹된 값. */
  auth_token?: string;
  /** BIZ-24 페이로드 한도(byte). */
  max_body_size?: number;
  /** BIZ-24 윈도당 허용 요청 수. ``0``이면 비활성. */
  rate_limit?: number;
  rate_limit_window?: number;
  max_concurrent_connections?: number;
  queue_size?: number;
  alert_cooldown?: number;
  [k: string]: unknown;
}

export interface ChannelsConfig {
  telegram?: TelegramConfig;
  webhook?: WebhookConfig;
}

/** ``classify_keys``의 응답. */
export interface PolicyResultDTO {
  level: "Hot" | "Service-restart" | "Process-restart";
  requires_restart: boolean;
  affected_modules: string[];
  matched_keys: string[];
}

export interface DryRunResponse {
  outcome: "dry_run";
  diff: { before: unknown; after: unknown };
  policy: PolicyResultDTO;
}

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

/** 채널 테스트 발송 응답 — admin_api._handle_test_channel과 동일 형태. */
export interface ChannelTestResponse {
  ok: boolean;
  status_code: number;
  latency_ms: number;
  target?: string;
  error?: string;
  response_preview?: string;
  audit_id?: string;
}

// ---------------------------------------------------------------------------
// 설정 — 조회 / dry-run / 적용
// ---------------------------------------------------------------------------

/** ``/admin/v1/config/channels`` — telegram + webhook 묶음 조회. */
export async function getChannelsConfig(): Promise<ChannelsConfig> {
  const data = await fetchAdmin<{ area: string; config: ChannelsConfig }>(
    "/admin/v1/config/channels",
  );
  return data.config ?? {};
}

/** Telegram 부분 dry-run — ``policy.level``과 ``diff``로 결과 확인. */
export function dryRunTelegramPatch(
  patch: Partial<TelegramConfig>,
): Promise<DryRunResponse> {
  return fetchAdmin<DryRunResponse>("/admin/v1/config/telegram", {
    method: "PATCH",
    json: patch,
    dryRun: true,
  });
}

/** Telegram 부분 적용 — Hot이면 즉시, Service-restart면 봇 재기동 트리거. */
export function applyTelegramPatch(
  patch: Partial<TelegramConfig>,
): Promise<ApplyResponse> {
  return fetchAdmin<ApplyResponse>("/admin/v1/config/telegram", {
    method: "PATCH",
    json: patch,
  });
}

export function dryRunWebhookPatch(
  patch: Partial<WebhookConfig>,
): Promise<DryRunResponse> {
  return fetchAdmin<DryRunResponse>("/admin/v1/config/webhook", {
    method: "PATCH",
    json: patch,
    dryRun: true,
  });
}

export function applyWebhookPatch(
  patch: Partial<WebhookConfig>,
): Promise<ApplyResponse> {
  return fetchAdmin<ApplyResponse>("/admin/v1/config/webhook", {
    method: "PATCH",
    json: patch,
  });
}

// ---------------------------------------------------------------------------
// 테스트 발송
// ---------------------------------------------------------------------------

/** 채널별 dry-run 메시지를 발송한다 — 결과 토스트로 status/latency 표시. */
export function testSendChannel(
  channel: "telegram" | "webhook",
  options: { message?: string; target?: string | number } = {},
): Promise<ChannelTestResponse> {
  return fetchAdmin<ChannelTestResponse>(
    `/admin/v1/channels/${encodeURIComponent(channel)}/test`,
    {
      method: "POST",
      json: {
        message: options.message ?? "Hello from admin",
        target: options.target,
      },
    },
  );
}

// ---------------------------------------------------------------------------
// 시크릿 — Channels 화면에서 bot_token / auth_token 회전을 위해 재사용.
// ---------------------------------------------------------------------------

export async function listSecrets(): Promise<SecretMeta[]> {
  const data = await fetchAdmin<{ secrets: SecretMeta[] }>(
    "/admin/v1/secrets",
  );
  return data.secrets ?? [];
}

export function revealSecret(
  name: string,
  backend?: string,
): Promise<RevealResponse> {
  return fetchAdmin<RevealResponse>(
    `/admin/v1/secrets/${encodeURIComponent(name)}/reveal`,
    {
      method: "POST",
      query: backend ? { backend } : undefined,
      json: {},
    },
  );
}

export function rotateSecret(
  name: string,
  value: string,
  backend?: string,
): Promise<{
  outcome: string;
  audit_id: string;
  backend: string;
  name: string;
}> {
  return fetchAdmin(`/admin/v1/secrets/${encodeURIComponent(name)}/rotate`, {
    method: "POST",
    json: { value, backend },
  });
}

export function undoAudit(
  id: string,
): Promise<{ outcome: string; audit_id: string }> {
  return fetchAdmin(`/admin/v1/audit/${encodeURIComponent(id)}/undo`, {
    method: "POST",
    json: {},
  });
}

// ---------------------------------------------------------------------------
// 헬퍼 — 시크릿 참조 문자열 파싱.
// ---------------------------------------------------------------------------

/** ``keyring:foo`` → ``{backend: "keyring", name: "foo"}``. 그 외는 ``{name: ref}``. */
export function parseSecretRef(
  ref: string | undefined,
): { backend?: string; name?: string } {
  if (!ref) return {};
  const m = /^(env|keyring|file):(.+)$/.exec(ref);
  if (!m) return { name: ref };
  return { backend: m[1], name: m[2] };
}
