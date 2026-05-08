/**
 * Memory 픽스처 — S8 (BIZ-119) 단계의 mock 데이터.
 *
 * admin.pen `fab0a` (Memory Shell) · `XFipm` (Active Projects 패널) ·
 * `oeMlR` (Insights) · `lVcRk` (Reject Confirm + Blocklist) ·
 * `ftlSL` (Source Drawer) · `QBf7N` (Dry-run Preview) 의 시각 spec 에
 * 1:1 매핑한다.
 *
 * 본 단계는 Memory/Dreaming 데몬 API 가 아직 미연결이므로 정적 fixture 만
 * 노출한다. 후속 sub-issue (데몬 통합 단계) 가 본 모듈만 비동기로 교체하면
 * 호출부 변경이 없도록, 컴포넌트는 fixture 를 직접 import 하지 않고
 * `getMemorySnapshot()` 한 함수만 호출한다.
 */
import type { MemoryCluster } from "@/design/domain/MemoryClusterMap";

/** 인사이트 큐 한 건 — admin.pen `oeMlR` Review 카드와 1:1. */
export interface MemoryInsight {
  /** 영구 키 — slug. */
  id: string;
  /** 토픽 정규형 — 블록리스트 키와 동일한 값 (case-insensitive). */
  topic: string;
  /** USER.md 에 적용될 본문 (한 줄). */
  text: string;
  /** 0..1 — 채택 임계는 0.6 권장. */
  confidence: number;
  /** 근거 메시지 수 — Source Drawer 의 좌상단 카운트. */
  evidenceCount: number;
  /** 마지막 관찰 — ISO. */
  updatedAt: string;
  /** 라이프사이클 상태 — 4-탭 분류의 SSOT. */
  lifecycle: "review" | "active" | "archive";
  /**
   * 추출 출처 채널 — telegram/voice/web 등. Source Drawer 의 메타에 노출.
   * 본 단계는 단일값. 다채널 인사이트 표현은 후속 sub-issue.
   */
  channel: string;
  /**
   * cron/recipe 자동 실행에서 추출된 noise 후보 — UI 에서 reject-only 로 노출.
   * 백엔드 라벨이 들어오면 휴리스틱은 제거.
   */
  cronNoise?: boolean;
}

/** 블록리스트 한 건 — admin.pen `lVcRk` Blocklist 표의 한 줄. */
export interface BlocklistEntry {
  /** 정규형 키 — 토픽 case-insensitive. */
  topicKey: string;
  /** 사람이 보는 토픽 라벨. */
  topic: string;
  /** 사용자가 입력한 사유 — 비어있을 수 있음. */
  reason: string;
  /** 차단 시각 — ISO. */
  blockedAt: string;
  /**
   * 차단 만료 — null 이면 영구. admin.pen Reject Confirm 의 단일 셀렉트와 정렬:
   * 7d / 30d / forever.
   */
  expiresAt: string | null;
}

/** Source Drawer 가 노출하는 근거 메시지 한 건 — `ftlSL` 의 한 줄. */
export interface SourceMessage {
  id: string;
  /** 채널/봇 식별자 — telegram/voice/web 등. */
  channel: string;
  /** user / assistant. */
  role: "user" | "assistant";
  /** 보낸 시각 — ISO. */
  timestamp: string;
  /** 본문. */
  content: string;
  /** 원본 conversation 링크 (있는 경우). */
  permalink?: string;
}

/** Active Projects 한 건 — admin.pen `XFipm` 패널의 한 줄. */
export interface ActiveProject {
  id: string;
  title: string;
  /** dreaming cluster score (0..1). */
  score: number;
  /** managed marker 보유 여부 — true 면 dreaming decay 에서 제외. */
  managed: boolean;
  /** 마지막 갱신 — ISO. */
  updatedAt: string;
}

/** Dry-run Preview 결과 — `QBf7N` 의 미리보기 카드 데이터. */
export interface DryRunChange {
  /** 라이프사이클 변화 종류. */
  kind: "promote" | "edit" | "archive" | "block";
  /** 대상 토픽. */
  topic: string;
  /** USER.md 의 현재 한 줄 (없으면 null = 신규). */
  before: string | null;
  /** 미리보기 결과 한 줄. */
  after: string | null;
  /** 한 줄 영향 요약. */
  reason: string;
}

export interface DryRunPreview {
  /** 시뮬레이션 시각 — ISO. */
  generatedAt: string;
  /** 평가된 후보 수. */
  candidateCount: number;
  /** 적용 예정 변경. */
  changes: readonly DryRunChange[];
}

export interface MemorySnapshot {
  insights: readonly MemoryInsight[];
  blocklist: readonly BlocklistEntry[];
  /** Source Drawer 가 보여줄 메시지 — insightId → 메시지 배열. */
  sources: Readonly<Record<string, readonly SourceMessage[]>>;
  activeProjects: readonly ActiveProject[];
  clusters: readonly MemoryCluster[];
  dryRun: DryRunPreview;
  /** 마지막 dreaming 사이클 — 헤더의 last-run 라인. */
  lastDreaming: {
    endedAt: string;
    status: "success" | "skip" | "error";
    generatedCount: number;
  } | null;
}

/**
 * Memory 가 화면에 그릴 모든 데이터를 한 번에 반환.
 * 실제 API 연동 시 본 함수만 비동기로 교체하면 호출부 변경이 없도록 정리.
 */
export function getMemorySnapshot(): MemorySnapshot {
  return {
    insights: INSIGHTS,
    blocklist: BLOCKLIST,
    sources: SOURCES,
    activeProjects: ACTIVE_PROJECTS,
    clusters: CLUSTERS,
    dryRun: DRY_RUN,
    lastDreaming: LAST_DREAMING,
  };
}

function nowMinus(minutes: number): string {
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

const INSIGHTS: readonly MemoryInsight[] = [
  {
    id: "ins-001",
    topic: "morning-briefing",
    text: "사용자는 평일 오전 8시 전후로 메일·일정 요약을 한 번에 보길 선호합니다.",
    confidence: 0.78,
    evidenceCount: 4,
    updatedAt: nowMinus(35),
    lifecycle: "review",
    channel: "telegram",
  },
  {
    id: "ins-002",
    topic: "stocks-watchlist",
    text: "AAPL · MSFT · NVDA 를 핵심 관심 종목으로 두고 있습니다.",
    confidence: 0.82,
    evidenceCount: 6,
    updatedAt: nowMinus(120),
    lifecycle: "review",
    channel: "telegram",
  },
  {
    id: "ins-003",
    topic: "cron.heartbeat.noise",
    text: "channel.heartbeat 크론은 5분마다 정상 응답합니다.",
    confidence: 0.32,
    evidenceCount: 12,
    updatedAt: nowMinus(15),
    lifecycle: "review",
    channel: "cron",
    cronNoise: true,
  },
  {
    id: "ins-004",
    topic: "tone.formal",
    text: "공식 발표문 작성 시 한국어 격식체를 선호합니다.",
    confidence: 0.91,
    evidenceCount: 9,
    updatedAt: nowMinus(60 * 24 * 2),
    lifecycle: "active",
    channel: "web",
  },
  {
    id: "ins-005",
    topic: "team-comms",
    text: "동료 호칭 시 직함 대신 이름 + 님 을 사용합니다.",
    confidence: 0.74,
    evidenceCount: 5,
    updatedAt: nowMinus(60 * 24 * 5),
    lifecycle: "active",
    channel: "telegram",
  },
  {
    id: "ins-006",
    topic: "meeting-notes-archived",
    text: "(보관) 2026-Q1 회고 회의록 형식 — markdown table.",
    confidence: 0.66,
    evidenceCount: 3,
    updatedAt: nowMinus(60 * 24 * 30),
    lifecycle: "archive",
    channel: "voice",
  },
];

const BLOCKLIST: readonly BlocklistEntry[] = [
  {
    topicKey: "joke.daily",
    topic: "joke.daily",
    reason: "일회성 농담 — 학습 가치 없음",
    blockedAt: nowMinus(60 * 24 * 7),
    expiresAt: null,
  },
  {
    topicKey: "weather.smalltalk",
    topic: "weather.smalltalk",
    reason: "주제가 너무 넓음 → 잘못된 일반화 위험",
    blockedAt: nowMinus(60 * 24 * 3),
    expiresAt: nowMinus(-60 * 24 * 27),
  },
];

const SOURCES: Readonly<Record<string, readonly SourceMessage[]>> = {
  "ins-001": [
    {
      id: "msg-101",
      channel: "telegram",
      role: "user",
      timestamp: nowMinus(60 * 9),
      content: "내일 아침 8시쯤 메일이랑 일정 한 번에 정리해줄 수 있을까?",
    },
    {
      id: "msg-102",
      channel: "telegram",
      role: "assistant",
      timestamp: nowMinus(60 * 9 - 1),
      content: "네, 매일 평일 8시에 morning-briefing 레시피로 보내드릴게요.",
    },
    {
      id: "msg-103",
      channel: "telegram",
      role: "user",
      timestamp: nowMinus(60 * 8),
      content: "좋아, 그렇게 해줘. 가능하면 8시 5분쯤이 더 좋아.",
    },
  ],
  "ins-002": [
    {
      id: "msg-201",
      channel: "telegram",
      role: "user",
      timestamp: nowMinus(60 * 24 * 2),
      content: "AAPL, MSFT, NVDA 시세 한 번에 확인할 수 있게 해줘.",
    },
    {
      id: "msg-202",
      channel: "telegram",
      role: "user",
      timestamp: nowMinus(60 * 12),
      content: "내 와치리스트 — AAPL/MSFT/NVDA 만 신경쓰면 돼.",
    },
  ],
  "ins-003": [
    {
      id: "msg-301",
      channel: "cron",
      role: "assistant",
      timestamp: nowMinus(20),
      content: "channel.heartbeat 정상 응답 (latency 320ms).",
    },
  ],
};

const ACTIVE_PROJECTS: readonly ActiveProject[] = [
  {
    id: "proj-admin-2",
    title: "Admin 2.0 박제 — admin.pen 기반 재구축",
    score: 0.92,
    managed: true,
    updatedAt: nowMinus(45),
  },
  {
    id: "proj-dreaming",
    title: "Dreaming 라이프사이클 안정화",
    score: 0.78,
    managed: true,
    updatedAt: nowMinus(60 * 6),
  },
  {
    id: "proj-stocks",
    title: "주식 워치리스트 자동 리포트",
    score: 0.61,
    managed: false,
    updatedAt: nowMinus(60 * 24),
  },
];

const CLUSTERS: readonly MemoryCluster[] = [
  {
    id: "cluster-tone",
    label: "어조·문체",
    count: 84,
    keywords: ["격식", "이모지", "한국어"],
    tone: "primary",
  },
  {
    id: "cluster-stocks",
    label: "주식·금융",
    count: 56,
    keywords: ["AAPL", "와치리스트", "리포트"],
    tone: "info",
  },
  {
    id: "cluster-routines",
    label: "일과·루틴",
    count: 42,
    keywords: ["아침", "브리핑", "스케줄"],
    tone: "success",
  },
  {
    id: "cluster-meetings",
    label: "회의·문서",
    count: 28,
    keywords: ["회고", "노트", "markdown"],
    tone: "warning",
  },
  {
    id: "cluster-noise",
    label: "노이즈 후보",
    count: 19,
    keywords: ["cron", "heartbeat", "스케줄"],
    tone: "error",
  },
];

const DRY_RUN: DryRunPreview = {
  generatedAt: nowMinus(2),
  candidateCount: 12,
  changes: [
    {
      kind: "promote",
      topic: "morning-briefing",
      before: null,
      after: "사용자는 평일 오전 8시 전후로 메일·일정 요약을 한 번에 보길 선호합니다.",
      reason: "신규 채택 — confidence 0.78 / 근거 4건",
    },
    {
      kind: "edit",
      topic: "stocks-watchlist",
      before: "AAPL · MSFT 를 핵심 관심 종목으로 둔다.",
      after: "AAPL · MSFT · NVDA 를 핵심 관심 종목으로 두고 있습니다.",
      reason: "본문 갱신 — NVDA 추가 관찰",
    },
    {
      kind: "block",
      topic: "cron.heartbeat.noise",
      before: null,
      after: null,
      reason: "cron 자동 실행 잡음 — 자동 차단 후보",
    },
  ],
};

const LAST_DREAMING = {
  endedAt: nowMinus(2 * 60),
  status: "success" as const,
  generatedCount: 7,
};
