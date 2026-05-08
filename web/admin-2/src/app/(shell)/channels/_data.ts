/**
 * Channels 픽스처 — S10 (BIZ-121) 단계의 mock 데이터.
 *
 * admin.pen `weuuW` (Channels Shell) · BIZ-109 P1 (Token Rotate ConfirmGate,
 * Webhook Edit modal, Traffic Simulation 미리보기) 시각 spec 에 1:1 매핑.
 *
 * 본 단계는 데몬 채널 API 가 아직 미연결이므로 정적 fixture 만 노출한다.
 * 후속 sub-issue (데몬 통합 단계) 가 본 모듈만 비동기로 교체하면 호출부 변경이
 * 없도록, 컴포넌트는 fixture 를 직접 import 하지 않고 `getChannelsSnapshot()`
 * 한 함수만 호출한다.
 */
import type { StatusTone } from "@/design/atoms/StatusPill";

/** Telegram 봇 채널 — `weuuW` 상단 카드 SSOT. */
export interface TelegramChannel {
  /** 봇 식별자 — `@SimpleClawBot` 같은 username (마스킹 미적용). */
  username: string;
  /** Bot Token 마스킹 표시 — secret URI 와 함께 노출. */
  tokenMasked: string;
  /** 토큰이 보관된 secret URI (예: `secret://TELEGRAM_BOT_TOKEN`). */
  tokenSecretUri: string;
  /** 허용된 chat ID 목록 — Allowlist 입력의 SSOT. */
  allowlist: readonly string[];
  /** 연결 상태 — `connected` / `disconnected` / `degraded`. */
  status: "connected" | "disconnected" | "degraded";
  /** 카드 헤더 우측 status pill 의 표시 라벨. */
  statusLabel: string;
  /** 헤더 우측 pill 의 시각 톤. */
  statusTone: StatusTone;
}

/** 웹훅 정책 — 카드 상단 4 입력의 SSOT. */
export interface WebhookPolicy {
  rateLimitPerSec: number;
  maxBodyKb: number;
  concurrency: number;
  /** 서명 알고리즘 — `HMAC-SHA256` 등. */
  signature: string;
}

/** 웹훅 endpoint 한 건 — 카드 하단 표 한 행. */
export interface WebhookEndpoint {
  id: string;
  url: string;
  /** 사람 친화 용도 — `GitHub PR/Issue 알림` 같은 설명. */
  purpose: string;
  /** 24h 처리량 (요청 수). */
  reqLast24h: number;
  /** 활성/비활성 — Switch 의 SSOT. */
  enabled: boolean;
  /** 서명 시크릿 환경 변수 명 — Edit modal 의 prefill. */
  secretEnv: string;
  /** 개별 endpoint rate limit (req/s) — 전역 정책과 별도. */
  rateLimitPerSec: number;
  /** 개별 endpoint 동시성. */
  concurrency: number;
  /** Body JSON Schema 텍스트 — Edit modal 의 prefill. */
  bodySchema: string;
}

/** 웹훅 영역 전체 SSOT. */
export interface WebhooksConfig {
  policy: WebhookPolicy;
  endpoints: readonly WebhookEndpoint[];
  /** 카드 헤더 우측 메타: 24h 총 요청 수. */
  reqLast24hTotal: number;
  /** 24h 4xx 비율 (0..1). */
  errorRate24h: number;
}

/** 트래픽 시뮬레이션 미리보기 — Traffic Simulation 모달의 결과. */
export interface TrafficSimulation {
  /** 60초 동안 처리/대기/거부로 분류된 요청 비율 (합계 1). */
  served: number;
  queued: number;
  rejected: number;
  /** 60s stacked area 차트 — 시각화는 텍스트 placeholder 로 박제. */
  chartLabel: string;
}

export interface ChannelsSnapshot {
  telegram: TelegramChannel;
  webhooks: WebhooksConfig;
}

/**
 * Channels 가 화면에 그릴 모든 데이터를 한 번에 반환.
 * 실제 API 연동 시 본 함수만 비동기로 교체하면 호출부 변경이 없도록 정리.
 */
export function getChannelsSnapshot(): ChannelsSnapshot {
  return {
    telegram: TELEGRAM,
    webhooks: WEBHOOKS,
  };
}

const TELEGRAM: TelegramChannel = {
  username: "@SimpleClawBot",
  tokenMasked: "6•••••••:•••••••••••••••",
  tokenSecretUri: "secret://TELEGRAM_BOT_TOKEN",
  allowlist: ["123456789", "987654321"],
  status: "connected",
  statusLabel: "연결됨 · @SimpleClawBot",
  statusTone: "success",
};

const WEBHOOKS: WebhooksConfig = {
  policy: {
    rateLimitPerSec: 50,
    maxBodyKb: 512,
    concurrency: 8,
    signature: "HMAC-SHA256",
  },
  reqLast24hTotal: 1432,
  errorRate24h: 0.004,
  endpoints: [
    {
      id: "github",
      url: "https://hooks.simpleclaw.dev/github",
      purpose: "GitHub PR/Issue 알림",
      reqLast24h: 832,
      enabled: true,
      secretEnv: "WEBHOOK_GITHUB_SECRET",
      rateLimitPerSec: 30,
      concurrency: 4,
      bodySchema: '{ "type": "object" }',
    },
    {
      id: "multica",
      url: "https://hooks.simpleclaw.dev/multica",
      purpose: "Multica 이슈/코멘트 inbound",
      reqLast24h: 480,
      enabled: true,
      secretEnv: "WEBHOOK_MULTICA_SECRET",
      rateLimitPerSec: 20,
      concurrency: 4,
      bodySchema:
        '{\n  "type": "object",\n  "required": ["issue_id"],\n  "properties": { "issue_id": { "type": "string" } }\n}',
    },
    {
      id: "legacy-slack",
      url: "https://hooks.simpleclaw.dev/legacy-slack",
      purpose: "Slack legacy (deprecated)",
      reqLast24h: 120,
      enabled: false,
      secretEnv: "WEBHOOK_SLACK_SECRET",
      rateLimitPerSec: 10,
      concurrency: 4,
      bodySchema:
        '{\n  type: object,\n  required: [text],\n  properties: { text: {type: string, maxLength: 4000} }\n}',
    },
  ],
};

/**
 * 입력 부하 → 시뮬 결과 산출 — 본 단계는 결정론적 박제.
 *
 * - 처리 가능량 = min(req/s, rateLimit) * concurrency_factor
 * - burst 가 rateLimit*N 을 넘으면 거부 비율이 빠르게 증가
 * - 데몬 통합 단계에서 본 함수만 실제 시뮬레이터 응답으로 교체.
 */
export function simulateTraffic(input: {
  reqPerSec: number;
  burstPeak: number;
  concurrency: number;
  rateLimitPerSec: number;
}): TrafficSimulation {
  const { reqPerSec, burstPeak, concurrency, rateLimitPerSec } = input;
  const effective = Math.max(rateLimitPerSec, 1);
  // 정상 부하 — rateLimit 이하라면 모두 처리.
  const sustainedServedRatio = Math.min(reqPerSec / effective, 1);
  // 버스트가 effective * concurrency 를 넘으면 거부 비율이 비선형 증가.
  const burstHeadroom = effective * Math.max(concurrency, 1) * 0.6;
  const overflow = Math.max(burstPeak - burstHeadroom, 0);
  const rejected = Math.min(overflow / Math.max(burstPeak, 1), 0.95);
  // 대기는 burst 와 sustained 사이의 격차로 박제.
  const queued = Math.max(0, Math.min(0.4, (burstPeak - reqPerSec) / Math.max(burstPeak, 1)));
  const servedRaw = Math.max(0, sustainedServedRatio - rejected * 0.5);
  // 합계 1 정규화 — 시각화 안정성.
  const total = servedRaw + queued + rejected || 1;
  return {
    served: servedRaw / total,
    queued: queued / total,
    rejected: rejected / total,
    chartLabel: "[ TrafficChart · 60s · stacked area ]",
  };
}
