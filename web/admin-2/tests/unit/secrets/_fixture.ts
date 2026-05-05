/**
 * Secrets 단위 테스트용 공통 픽스처.
 */
import type { SecretRecord } from "@/app/(shell)/secrets/_data";

export const SECRET_HOT: SecretRecord = {
  id: "keyring:llm.test_api_key",
  keyName: "llm.test_api_key",
  scope: "llm-provider",
  maskedPreview: "••••a1f3",
  policy: "hot",
  lastRotatedAt: "2026-04-23T09:00:00.000Z",
  lastUsedAt: "2026-05-05T07:00:00.000Z",
  note: "Test 용 — Claude.",
};

export const SECRET_PROCESS_RESTART: SecretRecord = {
  id: "keyring:system.signing_key",
  keyName: "system.signing_key",
  scope: "system",
  maskedPreview: "••••0017",
  policy: "process-restart",
  lastRotatedAt: null,
  lastUsedAt: null,
};

export const SECRET_LIST: readonly SecretRecord[] = [
  SECRET_HOT,
  SECRET_PROCESS_RESTART,
  {
    id: "keyring:channel.tg_token",
    keyName: "channel.tg_token",
    scope: "channel",
    maskedPreview: "••••f72d",
    policy: "service-restart",
    lastRotatedAt: "2026-03-04T09:00:00.000Z",
    lastUsedAt: "2026-05-05T07:30:00.000Z",
  },
];
