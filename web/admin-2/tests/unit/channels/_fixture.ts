/**
 * Channels 단위 테스트용 공통 픽스처.
 */
import type {
  TelegramChannel,
  WebhookEndpoint,
  WebhookPolicy,
} from "@/app/(shell)/channels/_data";

export const TELEGRAM: TelegramChannel = {
  username: "@TestBot",
  tokenMasked: "6•••••••:•••••••••••••••",
  tokenSecretUri: "secret://TELEGRAM_BOT_TOKEN",
  allowlist: ["111", "222"],
  status: "connected",
  statusLabel: "연결됨 · @TestBot",
  statusTone: "success",
};

export const POLICY: WebhookPolicy = {
  rateLimitPerSec: 50,
  maxBodyKb: 512,
  concurrency: 8,
  signature: "HMAC-SHA256",
};

export const ENDPOINT: WebhookEndpoint = {
  id: "github",
  url: "https://hooks.simpleclaw.dev/github",
  purpose: "GitHub PR/Issue 알림",
  reqLast24h: 832,
  enabled: true,
  secretEnv: "WEBHOOK_GITHUB_SECRET",
  rateLimitPerSec: 30,
  concurrency: 4,
  bodySchema: '{ "type": "object" }',
};
