/**
 * Secrets 픽스처 — S9 (BIZ-120) 단계의 mock 데이터.
 *
 * admin.pen `M99Mh` (Secrets Shell) 의 콘텐츠를 React 로 박제한다.
 * 정책: PolicyChip 의 hot / service-restart / process-restart 와 1:1 매핑되며,
 * "이 시크릿이 회전되면 서비스 재시작이 필요한지" 를 운영자가 회전 전에 알 수
 * 있도록 시각으로 노출한다 (DESIGN.md §4.2).
 *
 * 보안 경계 — *마스킹된 미리보기* 만 fixture 에 포함하고, 평문(`••••1234` 의
 * 실제 값) 은 절대 렌더링되지 않도록 한다. SecretField 의 reveal 콜백은
 * 본 단계에서 console 에 토큰 fetch 의도만 박제하고, 실제 평문 노출은
 * 후속 sub-issue (Secrets API 통합) 가 책임진다.
 */
import type { PolicyKind } from "@/design/molecules/PolicyChip";

/** 한 시크릿 한 줄 — admin.pen `M99Mh` 표의 한 행. */
export interface SecretRecord {
  /** 영구 키 — `keyring:<scope>.<name>` 형식. */
  id: string;
  /** 사용자가 보는 키 이름 — Code atom 으로 mono 표시. */
  keyName: string;
  /** 카테고리 그룹 — 표시 구획 (provider / channel / system 등). */
  scope: SecretScope;
  /** 마스킹 미리보기 — 항상 `••••<last4>` 형식으로 사전계산. */
  maskedPreview: string;
  /** 회전 정책 — PolicyChip kind 와 1:1. */
  policy: PolicyKind;
  /** 마지막 회전 — ISO. null = 아직 회전한 적 없음 (초기 발급 직후). */
  lastRotatedAt: string | null;
  /** 마지막 사용 — ISO. null = 사용 이력 없음. */
  lastUsedAt: string | null;
  /** 운영자 메모 — 한 줄. */
  note?: string;
}

/** 시크릿 카테고리 — admin.pen 표의 시각 구획에 매핑. */
export type SecretScope =
  | "llm-provider"
  | "channel"
  | "system"
  | "service";

/** UI 노출용 라벨 — Sidebar/표 헤더에서 동일한 한국어로 노출. */
export const SCOPE_LABEL: Record<SecretScope, string> = {
  "llm-provider": "LLM 프로바이더",
  channel: "채널",
  system: "시스템",
  service: "외부 서비스",
};

/** Add Secret modal 의 키 이름 입력 검증 정규식. */
export const KEY_NAME_PATTERN = /^[a-z0-9][a-z0-9._-]{2,63}$/;

export interface SecretsSnapshot {
  secrets: readonly SecretRecord[];
  /** 사용 가능한 scope 옵션 — Add Secret modal 의 셀렉트. */
  scopes: readonly SecretScope[];
}

/**
 * Secrets 가 화면에 그릴 모든 데이터를 한 번에 반환.
 * 실제 keyring API 연동 시 본 함수만 비동기로 교체하면 호출부 변경이 없도록 정리.
 */
export function getSecretsSnapshot(): SecretsSnapshot {
  return {
    secrets: SECRETS,
    scopes: SCOPES,
  };
}

function nowMinus(minutes: number): string {
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

const SCOPES: readonly SecretScope[] = [
  "llm-provider",
  "channel",
  "system",
  "service",
];

const SECRETS: readonly SecretRecord[] = [
  {
    id: "keyring:llm.anthropic_api_key",
    keyName: "llm.anthropic_api_key",
    scope: "llm-provider",
    maskedPreview: "••••a1f3",
    policy: "hot",
    lastRotatedAt: nowMinus(60 * 24 * 12),
    lastUsedAt: nowMinus(3),
    note: "Claude Opus 4.6 — 라우터 default.",
  },
  {
    id: "keyring:llm.openai_api_key",
    keyName: "llm.openai_api_key",
    scope: "llm-provider",
    maskedPreview: "••••8c20",
    policy: "hot",
    lastRotatedAt: nowMinus(60 * 24 * 30),
    lastUsedAt: nowMinus(60 * 26),
  },
  {
    id: "keyring:llm.google_api_key",
    keyName: "llm.google_api_key",
    scope: "llm-provider",
    maskedPreview: "••••4e9b",
    policy: "hot",
    lastRotatedAt: null,
    lastUsedAt: nowMinus(60 * 24 * 3),
    note: "Gemini 2.5 Pro — 미회전 (발급 후 첫 사용).",
  },
  {
    id: "keyring:channel.telegram_bot_token",
    keyName: "channel.telegram_bot_token",
    scope: "channel",
    maskedPreview: "••••f72d",
    policy: "service-restart",
    lastRotatedAt: nowMinus(60 * 24 * 60),
    lastUsedAt: nowMinus(2),
    note: "회전 시 텔레그램 봇 프로세스 재시작 필요.",
  },
  {
    id: "keyring:system.signing_key",
    keyName: "system.signing_key",
    scope: "system",
    maskedPreview: "••••0017",
    policy: "process-restart",
    lastRotatedAt: nowMinus(60 * 24 * 200),
    lastUsedAt: nowMinus(60 * 24 * 1),
    note: "회전 시 데몬 전체 재시작 — 다운타임 발생.",
  },
  {
    id: "keyring:service.naver_search",
    keyName: "service.naver_search",
    scope: "service",
    maskedPreview: "••••3300",
    policy: "hot",
    lastRotatedAt: nowMinus(60 * 24 * 90),
    lastUsedAt: nowMinus(60 * 24 * 5),
  },
];
