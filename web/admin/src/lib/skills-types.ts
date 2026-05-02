/**
 * Skills & Recipes 도메인 타입 — Admin Backend(BIZ-41 후속) 응답 스키마.
 *
 * 본 모듈은 Admin UI 측 SSOT다. 후속 백엔드 구현 시 이 타입을 ``json-schema`` 또는
 * pydantic 모델과 1:1로 정렬해야 하며, 변경이 발생하면 본 파일을 먼저 갱신한 뒤
 * 백엔드 측을 따라가도록 한다(프론트 우선 합의 — admin-requirements.md §2).
 */

/** 스킬 실행 결과 상태. */
export type SkillRunStatus = "ok" | "error" | "timeout" | "skipped";

/** 재시도 정책 — daemon/cron_retry와 정렬되되 키는 스킬 단위 컨텍스트로 단순화. */
export interface RetryPolicy {
  /** 최대 시도 횟수 (1 = 재시도 없음). */
  max_attempts: number;
  /** 초기 백오프 (초). */
  backoff_seconds: number;
  /** 백오프 전략 — fixed/linear/exponential 중 하나. none = 즉시 재시도. */
  backoff_strategy: "none" | "fixed" | "linear" | "exponential";
}

/** 스킬 목록 항목 — 카드 표면에 필요한 최소 필드. */
export interface Skill {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  /** 발견 위치 — 로컬(.agent/skills) 또는 글로벌(~/.agents/skills). */
  source: "local" | "global";
  directory: string;
  argument_hint?: string;
  /** 사용자가 슬래시 명령으로 호출 가능한지. */
  user_invocable: boolean;
  retry_policy: RetryPolicy;
  /** 마지막 실행 요약 — 한 번도 실행되지 않았다면 null. */
  last_run: { started_at: string; status: SkillRunStatus; duration_ms: number; error?: string } | null;
}

/** 스킬 상세 — Drawer에서 SKILL.md 미리보기 용. */
export interface SkillDetail extends Skill {
  /** SKILL.md 본문(markdown). 미리보기 영역에서 mono-block으로 노출. */
  skill_md: string;
  /** Script Target 경로 — feature-skills.md 형식. */
  script_target?: string;
}

/** PATCH /admin/v1/skills/{id} 페이로드. */
export interface SkillPatch {
  enabled?: boolean;
  retry_policy?: Partial<RetryPolicy>;
}

/** 단일 실행 로그 항목. */
export interface SkillRun {
  id: string;
  started_at: string;
  duration_ms: number;
  status: SkillRunStatus;
  command: string;
  exit_code: number;
  /** 실패 시 시도 차수(자동 재시도 정책 결합). */
  attempt: number;
  error?: string;
}

/** 레시피 단계 — v1(step) / v2(instruction) 통합 표면. */
export interface RecipeStep {
  name: string;
  /** v2 instruction이면 ``instruction``, v1이면 ``command``/``prompt``/``skill``. */
  type: "skill" | "command" | "prompt" | "instruction";
  /** 요약 — UI에 표시할 1줄. */
  summary: string;
}

/** 레시피 목록 항목. */
export interface Recipe {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  /** 트리거 키워드(콤마 구분) — 슬래시 명령과는 별개. */
  trigger: string;
  /** 의존 스킬 id 목록. */
  skills: string[];
  /** 포맷 버전 — v1(step) / v2(instruction). */
  version: "v1" | "v2";
  steps: RecipeStep[];
  timeout_seconds: number;
}

/** PATCH /admin/v1/recipes/{id} 페이로드. */
export interface RecipePatch {
  enabled?: boolean;
}
