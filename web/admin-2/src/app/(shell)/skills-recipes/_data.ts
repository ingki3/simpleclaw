/**
 * Skills & Recipes 픽스처 — S6 (BIZ-117) 단계의 mock 데이터.
 *
 * admin.pen `GnNLO` (Skills & Recipes Shell) · BIZ-109 P1 (Skill Discovery Drawer,
 * Retry Policy 인라인 편집 modal) 의 시각 spec 에 1:1 매핑.
 *
 * 본 단계는 스킬/레시피 데몬 API 가 아직 미연결이므로 정적 fixture 만 노출한다.
 * 후속 sub-issue (데몬 통합 단계) 가 본 모듈만 비동기로 교체하면 호출부 변경이
 * 없도록, 컴포넌트는 fixture 를 직접 import 하지 않고 `getSkillsRecipesSnapshot()`
 * 한 함수만 호출한다.
 */
import type { StatusTone } from "@/design/atoms/StatusPill";

/** 스킬 실행 상태 — admin/v1/skills/{id}/runs.last 와 정렬. */
export type SkillRunStatus = "ok" | "error" | "timeout" | "skipped";

/** 백오프 전략 — daemon/cron_retry 와 정렬. */
export type BackoffStrategy = "none" | "fixed" | "linear" | "exponential";

/** 재시도 정책 — admin.pen Retry Policy 모달과 1:1 매핑. */
export interface RetryPolicy {
  /** 최대 시도 횟수 (1 = 재시도 없음). */
  maxAttempts: number;
  /** 초기 백오프 (초). */
  backoffSeconds: number;
  /** 백오프 전략. none = 즉시 재시도. */
  backoffStrategy: BackoffStrategy;
  /** 단일 시도 최대 실행 시간 (초). */
  timeoutSeconds: number;
}

/** 마지막 실행 요약 — 카드 우상단 status pill 의 SSOT. */
export interface SkillRunSummary {
  startedAt: string;
  status: SkillRunStatus;
  durationMs: number;
  error?: string;
}

/** 카탈로그/설치 스킬 공통 베이스. */
interface SkillBase {
  /** 영구 키 — slug. */
  id: string;
  /** 사람이 보는 라벨. */
  name: string;
  /** 한 줄 설명. */
  description: string;
  /** 발견 위치 — 로컬(.agent/skills) 또는 글로벌(~/.agents/skills). */
  source: "local" | "global";
  /** 사용자가 슬래시 명령으로 호출 가능한지. */
  userInvocable: boolean;
}

/** 설치된 스킬 한 건 — admin.pen `GnNLO` 좌측 목록의 카드. */
export interface InstalledSkill extends SkillBase {
  /** 활성/비활성 — Switch 의 SSOT. */
  enabled: boolean;
  /** 스킬 디렉터리 경로. */
  directory: string;
  /** 슬래시 명령 인자 힌트. */
  argumentHint?: string;
  /** 재시도 정책 — Retry Policy 모달의 prefill 대상. */
  retryPolicy: RetryPolicy;
  /** 마지막 실행 요약 — 한 번도 실행되지 않았다면 null. */
  lastRun: SkillRunSummary | null;
  /** 카드 헬스 — 시각적 재확인용 (라벨이 SSOT). */
  health: { tone: StatusTone; label: string };
}

/** 카탈로그 스킬 한 건 — Discovery Drawer 의 후보. */
export interface CatalogSkill extends SkillBase {
  /** 발행자 — `anthropic`, `simpleclaw` 등. */
  publisher: string;
  /** 키워드 — Drawer 의 검색 매칭에 사용. */
  keywords: readonly string[];
  /** 짧은 카테고리 라벨 — Drawer 의 그룹 표기 (예: 생산성/금융). */
  category: string;
  /** 이미 설치되었는지 — true 면 카드에서 "추가" 가 비활성화된다. */
  installed: boolean;
}

/** 레시피 단계 — v1(step) / v2(instruction) 통합 표면. */
export interface RecipeStep {
  name: string;
  type: "skill" | "command" | "prompt" | "instruction";
  summary: string;
}

/** 레시피 한 건 — admin.pen `GnNLO` 우측 목록의 카드. */
export interface Recipe {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  /** 트리거 키워드(콤마 구분) — 슬래시 명령과는 별개. */
  trigger: string;
  /** 의존 스킬 id 목록. */
  skills: readonly string[];
  /** 포맷 버전. */
  version: "v1" | "v2";
  steps: readonly RecipeStep[];
  /** 전체 타임아웃 (초). */
  timeoutSeconds: number;
}

export interface SkillsRecipesSnapshot {
  skills: readonly InstalledSkill[];
  recipes: readonly Recipe[];
  /** Skill Discovery Drawer 의 카탈로그. */
  catalog: readonly CatalogSkill[];
}

/**
 * Skills & Recipes 가 화면에 그릴 모든 데이터를 한 번에 반환.
 * 실제 API 연동 시 본 함수만 비동기로 교체하면 호출부 변경이 없도록 정리.
 */
export function getSkillsRecipesSnapshot(): SkillsRecipesSnapshot {
  return {
    skills: SKILLS,
    recipes: RECIPES,
    catalog: CATALOG,
  };
}

function nowMinus(minutes: number): string {
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

const SKILLS: readonly InstalledSkill[] = [
  {
    id: "gmail-skill",
    name: "gmail-skill",
    description: "Gmail 에서 메일을 검색하고 읽는 스킬",
    source: "global",
    userInvocable: true,
    enabled: true,
    directory: "~/.agents/skills/gmail-skill",
    argumentHint: "검색어 또는 메일 ID",
    retryPolicy: {
      maxAttempts: 3,
      backoffSeconds: 2,
      backoffStrategy: "exponential",
      timeoutSeconds: 30,
    },
    lastRun: { startedAt: nowMinus(12), status: "ok", durationMs: 1820 },
    health: { tone: "success", label: "최근 정상 · 12분 전" },
  },
  {
    id: "google-calendar-skill",
    name: "google-calendar-skill",
    description: "Google 캘린더 일정을 조회·생성한다.",
    source: "global",
    userInvocable: true,
    enabled: true,
    directory: "~/.agents/skills/google-calendar-skill",
    argumentHint: "날짜 또는 이벤트 제목",
    retryPolicy: {
      maxAttempts: 2,
      backoffSeconds: 5,
      backoffStrategy: "fixed",
      timeoutSeconds: 20,
    },
    lastRun: { startedAt: nowMinus(58), status: "ok", durationMs: 940 },
    health: { tone: "success", label: "최근 정상 · 58분 전" },
  },
  {
    id: "naver-shopping-skill",
    name: "naver-shopping-skill",
    description: "네이버 쇼핑 상품과 가격을 조회한다.",
    source: "global",
    userInvocable: false,
    enabled: false,
    directory: "~/.agents/skills/naver-shopping-skill",
    argumentHint: "검색어",
    retryPolicy: {
      maxAttempts: 1,
      backoffSeconds: 0,
      backoffStrategy: "none",
      timeoutSeconds: 15,
    },
    lastRun: {
      startedAt: nowMinus(60 * 24 * 2),
      status: "error",
      durationMs: 8200,
      error: "rate-limit",
    },
    health: { tone: "error", label: "최근 실패 · rate-limit" },
  },
  {
    id: "us-stock-skill",
    name: "us-stock-skill",
    description: "미국 주식 시세·뉴스·기본정보를 조회한다.",
    source: "global",
    userInvocable: true,
    enabled: true,
    directory: "~/.agents/skills/us-stock-skill",
    argumentHint: "티커 (예: AAPL)",
    retryPolicy: {
      maxAttempts: 3,
      backoffSeconds: 1,
      backoffStrategy: "exponential",
      timeoutSeconds: 25,
    },
    lastRun: { startedAt: nowMinus(180), status: "ok", durationMs: 2200 },
    health: { tone: "success", label: "최근 정상 · 3시간 전" },
  },
  {
    id: "news-search-skill",
    name: "news-search-skill",
    description: "Gemini 그라운딩으로 최신 뉴스를 검색·요약한다.",
    source: "global",
    userInvocable: true,
    enabled: true,
    directory: "~/.agents/skills/news-search-skill",
    argumentHint: "검색 키워드",
    retryPolicy: {
      maxAttempts: 2,
      backoffSeconds: 3,
      backoffStrategy: "exponential",
      timeoutSeconds: 30,
    },
    lastRun: null,
    health: { tone: "neutral", label: "실행 이력 없음" },
  },
];

const RECIPES: readonly Recipe[] = [
  {
    id: "morning-briefing",
    name: "morning-briefing",
    description: "아침 브리핑 — 메일과 캘린더 요약",
    enabled: true,
    trigger: "아침 브리핑, morning briefing",
    skills: ["gmail-skill", "google-calendar-skill"],
    version: "v2",
    steps: [
      {
        name: "메일 확인",
        type: "skill",
        summary: "gmail-skill 호출 — 읽지 않은 메일 조회",
      },
      {
        name: "일정 확인",
        type: "skill",
        summary: "google-calendar-skill 호출 — 오늘 일정 조회",
      },
      {
        name: "요약 작성",
        type: "instruction",
        summary: "중요도별로 요약을 정리해 응답",
      },
    ],
    timeoutSeconds: 120,
  },
  {
    id: "evening-stocks",
    name: "evening-stocks",
    description: "저녁 보유 종목 시세 정리",
    enabled: false,
    trigger: "주식 정리, 저녁 시세",
    skills: ["us-stock-skill"],
    version: "v2",
    steps: [
      {
        name: "시세 조회",
        type: "skill",
        summary: "us-stock-skill 호출 — 보유 종목 시세 일괄 조회",
      },
      {
        name: "리포트",
        type: "instruction",
        summary: "전일 대비 상승/하락 종목을 표로 정리",
      },
    ],
    timeoutSeconds: 90,
  },
];

const CATALOG: readonly CatalogSkill[] = [
  {
    id: "gmail-skill",
    name: "gmail-skill",
    description: "Gmail 에서 메일을 검색하고 읽는 스킬",
    publisher: "simpleclaw",
    source: "global",
    userInvocable: true,
    keywords: ["gmail", "mail", "메일", "이메일", "google"],
    category: "생산성",
    installed: true,
  },
  {
    id: "google-calendar-skill",
    name: "google-calendar-skill",
    description: "Google 캘린더 일정을 조회·생성한다.",
    publisher: "simpleclaw",
    source: "global",
    userInvocable: true,
    keywords: ["calendar", "schedule", "일정", "캘린더"],
    category: "생산성",
    installed: true,
  },
  {
    id: "google-contacts-skill",
    name: "google-contacts-skill",
    description: "Google 연락처를 조회·관리한다.",
    publisher: "simpleclaw",
    source: "global",
    userInvocable: true,
    keywords: ["contacts", "address", "연락처"],
    category: "생산성",
    installed: false,
  },
  {
    id: "google-docs-skill",
    name: "google-docs-skill",
    description: "Google Docs 문서를 읽고 수정한다.",
    publisher: "simpleclaw",
    source: "global",
    userInvocable: true,
    keywords: ["docs", "document", "문서", "google"],
    category: "생산성",
    installed: false,
  },
  {
    id: "naver-shopping-skill",
    name: "naver-shopping-skill",
    description: "네이버 쇼핑 상품과 가격을 조회한다.",
    publisher: "simpleclaw",
    source: "global",
    userInvocable: false,
    keywords: ["shopping", "price", "쇼핑", "가격", "naver"],
    category: "쇼핑",
    installed: true,
  },
  {
    id: "us-stock-skill",
    name: "us-stock-skill",
    description: "미국 주식 시세·뉴스·기본정보를 조회한다.",
    publisher: "simpleclaw",
    source: "global",
    userInvocable: true,
    keywords: ["stock", "us", "주식", "시세"],
    category: "금융",
    installed: true,
  },
  {
    id: "kr-stock-skill",
    name: "kr-stock-skill",
    description: "국내 KOSPI/KOSDAQ 종목 시세와 공시를 조회한다.",
    publisher: "community",
    source: "global",
    userInvocable: true,
    keywords: ["stock", "kr", "kospi", "kosdaq", "주식"],
    category: "금융",
    installed: false,
  },
  {
    id: "news-search-skill",
    name: "news-search-skill",
    description: "Gemini 그라운딩으로 최신 뉴스를 검색·요약한다.",
    publisher: "simpleclaw",
    source: "global",
    userInvocable: true,
    keywords: ["news", "search", "뉴스", "검색"],
    category: "정보",
    installed: true,
  },
  {
    id: "local-route-skill",
    name: "local-route-skill",
    description: "지역 검색 + Google 지도 길찾기를 결합한다.",
    publisher: "simpleclaw",
    source: "global",
    userInvocable: true,
    keywords: ["map", "route", "directions", "길찾기", "지도"],
    category: "이동",
    installed: false,
  },
  {
    id: "weather-skill",
    name: "weather-skill",
    description: "현재 위치 또는 지정 도시의 날씨를 조회한다.",
    publisher: "community",
    source: "global",
    userInvocable: true,
    keywords: ["weather", "날씨"],
    category: "정보",
    installed: false,
  },
];
