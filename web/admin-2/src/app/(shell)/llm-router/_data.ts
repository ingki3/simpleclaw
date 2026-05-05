/**
 * LLM Router 픽스처 — S4 (BIZ-115) 단계의 mock 데이터.
 *
 * admin.pen `BBA7M` (LLM Router Shell) · `oUzPN` (Add Provider) ·
 * `AzGck` (Edit Provider) · `Sms7l` (Routing Rule Editor) 의 시각 spec 에 1:1 매핑.
 *
 * 본 단계는 실제 LLM 라우터 데몬 API 가 아직 미연결이므로 정적 fixture 만 노출한다.
 * 후속 sub-issue (S5 Skills/Recipes 또는 데몬 통합 단계) 가 본 모듈만 비동기로 교체하면 되도록,
 * 컴포넌트는 fixture 를 직접 import 하지 않고 `getRouterSnapshot()` 한 함수만 호출한다.
 */
import type { StatusTone } from "@/design/atoms/StatusPill";

/** Provider 한 건 — admin.pen `BBA7M` 카드와 `AzGck` Edit modal 양쪽이 본 타입을 공유. */
export interface RouterProvider {
  /** 영구 키 — `claude`, `openai`, `gemini` 같은 lowercase slug. */
  id: string;
  /** 사람이 보는 라벨 — 카드 제목, 모달 헤더. */
  name: string;
  /** API 종류 — Edit Provider modal 의 prefill 시 사용 (claude/openai/gemini/...). */
  apiType: "anthropic" | "openai" | "gemini" | "custom";
  /** 현재 활성 모델 — 카드 본문 첫 줄. */
  model: string;
  /** API base URL — `api.anthropic.com/v1` 형식. */
  baseUrl: string;
  /** 시크릿 키 마스킹 표시용 — 마지막 4자리 노출. 실제 값은 reveal 시 부모가 fetch. */
  apiKeyMasked: string;
  /** keyring 항목명 (carded by SecretField hint). */
  keyringName: string;
  /** 카드 우상단 default 뱃지 표기 여부. */
  isDefault: boolean;
  /** Fallback 체인 포함 여부. */
  inFallbackChain: boolean;
  /** Fallback 체인 내 우선순위 (0-based). 미포함 시 null. */
  fallbackPriority: number | null;
  /** 현재 헬스 — 카드 하단 StatusPill. */
  health: ProviderHealth;
}

/** 카드 하단 헬스 라인 — tone + 한 줄 설명. */
export interface ProviderHealth {
  tone: StatusTone;
  /** 한 줄 라벨 — "정상 · 350ms avg" / "1m 전 401 unauthorized". */
  label: string;
  /** 한 줄 보조 설명 — 디버깅 단서 (옵션). */
  detail?: string;
  /** 평균 지연 — MetricCard 좌측 값. */
  avgLatencyMs: number;
  /** 24h 토큰 처리량 — MetricCard 우측 값 (k 단위 사람표기 포함 문자열). */
  tokens24h: string;
}

/** 라우팅 규칙 한 건 — admin.pen `Sms7l` Routing Rule Editor 와 1:1 매핑. */
export interface RoutingRule {
  id: string;
  name: string;
  /** 트리거 — prompt 키워드 / 정규식 (modal 의 1줄 입력). */
  trigger: string;
  /** 우선순위가 매겨진 provider 목록. 첫 항목이 1순위. */
  providerOrder: RoutingRuleProvider[];
  /** 비용 한도 (USD/일). */
  dailyBudgetUsd: number;
}

export interface RoutingRuleProvider {
  /** RouterProvider.id 에 매칭. */
  providerId: string;
  /** 표시용 라벨 (provider 표시명 + 모델명). */
  label: string;
  /** 단가 표기 ("$15/MTok"). */
  rate: string;
  /** 평균 지연 표기 ("~2.4s"). */
  latency: string;
  /** "fallback" 등 보조 라벨. */
  badge?: string;
}

export interface RouterSnapshot {
  defaultProviderId: string;
  providers: readonly RouterProvider[];
  fallbackChain: readonly string[];
  rules: readonly RoutingRule[];
}

/**
 * LLM Router 가 화면에 그릴 모든 데이터를 한 번에 반환.
 * 실제 API 연동 시 본 함수 시그니처만 비동기로 교체하면 호출부 변경이 없도록 정리.
 */
export function getRouterSnapshot(): RouterSnapshot {
  return {
    defaultProviderId: "claude",
    providers: PROVIDERS,
    fallbackChain: ["claude", "openai", "gemini"],
    rules: RULES,
  };
}

const PROVIDERS: readonly RouterProvider[] = [
  {
    id: "claude",
    name: "claude",
    apiType: "anthropic",
    model: "claude-opus-4-20250814",
    baseUrl: "api.anthropic.com/v1",
    apiKeyMasked: "sk-ant-••••••••3a72",
    keyringName: "claude_api_key",
    isDefault: true,
    inFallbackChain: true,
    fallbackPriority: 0,
    health: {
      tone: "success",
      label: "정상 · 350ms avg",
      detail: "최근 24h 호출 4.6k 회 / 0 fail",
      avgLatencyMs: 350,
      tokens24h: "44.0k",
    },
  },
  {
    id: "openai",
    name: "openai",
    apiType: "openai",
    model: "gpt-4o",
    baseUrl: "api.openai.com/v1",
    apiKeyMasked: "sk-proj-••••••••a02b",
    keyringName: "openai_api_key",
    isDefault: false,
    inFallbackChain: true,
    fallbackPriority: 1,
    health: {
      tone: "success",
      label: "정상 · 530ms avg",
      detail: "fallback 으로 진입 시에만 호출됩니다.",
      avgLatencyMs: 530,
      tokens24h: "9.3k",
    },
  },
  {
    id: "gemini",
    name: "gemini",
    apiType: "gemini",
    model: "gemini-2.5-flash-preview",
    baseUrl: "env: GOOGLE_API_KEY",
    apiKeyMasked: "env: ••••••••KEY",
    keyringName: "GOOGLE_API_KEY",
    isDefault: false,
    inFallbackChain: true,
    fallbackPriority: 2,
    health: {
      tone: "error",
      label: "401 — 1분 전 fetch failed",
      detail: "키 권한 만료 가능성 — 회전 또는 재발급이 필요합니다.",
      avgLatencyMs: 0,
      tokens24h: "0",
    },
  },
];

const RULES: readonly RoutingRule[] = [
  {
    id: "rule-code",
    name: "코드 생성 작업 → Opus 우선",
    trigger: "intent: code|refactor|implement",
    providerOrder: [
      {
        providerId: "claude",
        label: "Claude · claude-opus-4-6",
        rate: "$15/MTok",
        latency: "~2.4s",
      },
      {
        providerId: "claude",
        label: "Claude · claude-sonnet-4-6",
        rate: "$3/MTok",
        latency: "~1.6s",
      },
      {
        providerId: "gemini",
        label: "Gemini · gemini-2.5-pro (fallback)",
        rate: "$1.25/MTok",
        latency: "~1.2s",
        badge: "fallback",
      },
    ],
    dailyBudgetUsd: 50,
  },
];
