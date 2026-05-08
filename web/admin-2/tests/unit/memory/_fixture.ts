/**
 * Memory 단위 테스트용 공통 픽스처.
 */
import type {
  ActiveProject,
  BlocklistEntry,
  DryRunPreview,
  MemoryInsight,
  SourceMessage,
} from "@/app/(shell)/memory/_data";

export const INSIGHT_REVIEW: MemoryInsight = {
  id: "ins-fix-001",
  topic: "morning-briefing",
  text: "사용자는 평일 오전 8시 전후로 메일·일정 요약을 한 번에 보길 선호합니다.",
  confidence: 0.78,
  evidenceCount: 4,
  updatedAt: "2026-05-05T09:00:00.000Z",
  lifecycle: "review",
  channel: "telegram",
};

export const INSIGHT_NOISE: MemoryInsight = {
  id: "ins-fix-002",
  topic: "cron.heartbeat.noise",
  text: "channel.heartbeat 크론은 5분마다 정상 응답합니다.",
  confidence: 0.32,
  evidenceCount: 12,
  updatedAt: "2026-05-05T09:30:00.000Z",
  lifecycle: "review",
  channel: "cron",
  cronNoise: true,
};

export const INSIGHT_LIST: readonly MemoryInsight[] = [
  INSIGHT_REVIEW,
  INSIGHT_NOISE,
];

export const PROJECT_MANAGED: ActiveProject = {
  id: "proj-fix-managed",
  title: "Admin 2.0 박제",
  score: 0.92,
  managed: true,
  updatedAt: "2026-05-05T09:00:00.000Z",
};

export const PROJECT_LIST: readonly ActiveProject[] = [
  PROJECT_MANAGED,
  {
    id: "proj-fix-fresh",
    title: "주식 워치리스트",
    score: 0.61,
    managed: false,
    updatedAt: "2026-05-04T09:00:00.000Z",
  },
];

export const BLOCKLIST_LIST: readonly BlocklistEntry[] = [
  {
    topicKey: "joke.daily",
    topic: "joke.daily",
    reason: "일회성 농담",
    blockedAt: "2026-04-29T09:00:00.000Z",
    expiresAt: null,
  },
  {
    topicKey: "weather.smalltalk",
    topic: "weather.smalltalk",
    reason: "주제 너무 넓음",
    blockedAt: "2026-05-02T09:00:00.000Z",
    expiresAt: "2026-06-02T09:00:00.000Z",
  },
];

export const SOURCE_MESSAGES: readonly SourceMessage[] = [
  {
    id: "msg-fix-1",
    channel: "telegram",
    role: "user",
    timestamp: "2026-05-05T08:00:00.000Z",
    content: "내일 아침 8시쯤 메일이랑 일정 한 번에 정리해줄 수 있을까?",
  },
  {
    id: "msg-fix-2",
    channel: "telegram",
    role: "assistant",
    timestamp: "2026-05-05T08:00:30.000Z",
    content: "네, 매일 평일 8시에 morning-briefing 레시피로 보내드릴게요.",
    permalink: "https://example.invalid/messages/msg-fix-2",
  },
];

export const DRY_RUN_PREVIEW: DryRunPreview = {
  generatedAt: "2026-05-05T08:00:00.000Z",
  candidateCount: 5,
  changes: [
    {
      kind: "promote",
      topic: "morning-briefing",
      before: null,
      after: "사용자는 평일 오전 8시에 브리핑을 선호합니다.",
      reason: "신규 채택 — confidence 0.78",
    },
    {
      kind: "edit",
      topic: "stocks-watchlist",
      before: "AAPL · MSFT 만 관찰",
      after: "AAPL · MSFT · NVDA 를 관찰",
      reason: "본문 갱신",
    },
    {
      kind: "block",
      topic: "cron.heartbeat.noise",
      before: null,
      after: null,
      reason: "cron 자동 실행 잡음",
    },
  ],
};
