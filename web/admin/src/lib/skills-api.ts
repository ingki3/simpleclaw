/**
 * Skills & Recipes API 클라이언트.
 *
 * BIZ-47의 Skills & Recipes 화면이 호출하는 백엔드 엔드포인트를 한 곳에 모은다.
 * 호출 자체는 fetch 래퍼로 단순화하고, 실패 시(엔드포인트 미구현·네트워크 오류)에는
 * 개발 모드에 한해 인메모리 mock으로 폴백한다 — 운영 빌드에서는 폴백 없이 에러를 그대로
 * 노출해 백엔드 부재를 드러낸다(BIZ-41 후속 보고 트리거).
 *
 * 엔드포인트 (BIZ-41 후속에서 신설 예정):
 *   GET    /admin/v1/skills                 — 디스커버리된 스킬 목록
 *   PATCH  /admin/v1/skills/{id}            — 활성/비활성, 재시도 정책 변경
 *   GET    /admin/v1/skills/{id}            — SKILL.md 본문 + 메타
 *   GET    /admin/v1/skills/{id}/runs       — 실행 로그 (limit, before 커서)
 *   GET    /admin/v1/recipes                — 레시피 목록
 *   PATCH  /admin/v1/recipes/{id}           — 활성/비활성 변경
 *
 * 활성/비활성 메타 키는 현행 ``skills.yaml``/``recipes.yaml``에 정의되지 않았으며,
 * BIZ-37 후속 권고에 따라 BIZ-41 후속 이슈에서 신설되어야 한다 — 본 모듈의 mock은
 * 그 명세 초안 역할을 겸한다.
 */

import type {
  Recipe,
  RecipePatch,
  Skill,
  SkillDetail,
  SkillPatch,
  SkillRun,
} from "./skills-types";

/** Admin 백엔드 베이스 URL — `NEXT_PUBLIC_ADMIN_API_BASE`로 override 가능. */
const API_BASE =
  process.env.NEXT_PUBLIC_ADMIN_API_BASE ?? "http://127.0.0.1:8082";

/** 운영 빌드에서는 mock 폴백을 비활성화한다 — 백엔드 미구현이 가시화되도록. */
const ALLOW_MOCK_FALLBACK = process.env.NODE_ENV !== "production";

class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    throw new ApiError(`${res.status} ${res.statusText}`, res.status);
  }
  return (await res.json()) as T;
}

/**
 * fetch 호출을 시도하고, 실패 시(개발 모드 한정) mock 폴백을 호출한다.
 *
 * Why: BIZ-41이 아직 ``/admin/v1/skills/*`` 엔드포인트를 구현하지 않았다.
 * 본 페이지는 디자인·UX 검증이 가능해야 하므로 개발 빌드에서는 mock으로 작동시키되,
 * 운영 빌드에서는 폴백을 끊어 백엔드 신설 누락을 즉시 드러낸다.
 */
async function withMockFallback<T>(
  realFetch: () => Promise<T>,
  mock: () => T,
): Promise<T> {
  try {
    return await realFetch();
  } catch (error) {
    if (!ALLOW_MOCK_FALLBACK) throw error;
    if (typeof window !== "undefined") {
      // dev 콘솔에서만 식별 가능하도록 — 운영 빌드는 위에서 throw로 끊긴다.
      // eslint-disable-next-line no-console
      console.warn(
        "[skills-api] 백엔드 호출 실패, mock 폴백을 사용합니다:",
        error,
      );
    }
    return mock();
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export async function listSkills(): Promise<Skill[]> {
  return withMockFallback(
    () => request<Skill[]>("/admin/v1/skills"),
    () => mockState.skills.map(stripDetail),
  );
}

export async function getSkill(id: string): Promise<SkillDetail> {
  return withMockFallback(
    () => request<SkillDetail>(`/admin/v1/skills/${encodeURIComponent(id)}`),
    () => {
      const found = mockState.skills.find((s) => s.id === id);
      if (!found) throw new ApiError("not found", 404);
      return found;
    },
  );
}

export async function patchSkill(
  id: string,
  patch: SkillPatch,
): Promise<Skill> {
  return withMockFallback(
    () =>
      request<Skill>(`/admin/v1/skills/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      }),
    () => {
      const idx = mockState.skills.findIndex((s) => s.id === id);
      if (idx < 0) throw new ApiError("not found", 404);
      const next = { ...mockState.skills[idx] };
      if (patch.enabled !== undefined) next.enabled = patch.enabled;
      if (patch.retry_policy) {
        next.retry_policy = { ...next.retry_policy, ...patch.retry_policy };
      }
      mockState.skills[idx] = next;
      return stripDetail(next);
    },
  );
}

export async function listSkillRuns(
  id: string,
  limit = 20,
): Promise<SkillRun[]> {
  return withMockFallback(
    () =>
      request<SkillRun[]>(
        `/admin/v1/skills/${encodeURIComponent(id)}/runs?limit=${limit}`,
      ),
    () => mockState.runs[id]?.slice(0, limit) ?? [],
  );
}

export async function listRecipes(): Promise<Recipe[]> {
  return withMockFallback(
    () => request<Recipe[]>("/admin/v1/recipes"),
    () => mockState.recipes,
  );
}

export async function patchRecipe(
  id: string,
  patch: RecipePatch,
): Promise<Recipe> {
  return withMockFallback(
    () =>
      request<Recipe>(`/admin/v1/recipes/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      }),
    () => {
      const idx = mockState.recipes.findIndex((r) => r.id === id);
      if (idx < 0) throw new ApiError("not found", 404);
      const next = { ...mockState.recipes[idx] };
      if (patch.enabled !== undefined) next.enabled = patch.enabled;
      mockState.recipes[idx] = next;
      return next;
    },
  );
}

// ---------------------------------------------------------------------------
// Mock 데이터 — 개발 모드 전용
// ---------------------------------------------------------------------------

function stripDetail(detail: SkillDetail): Skill {
  const {
    skill_md: _skill_md,
    script_target: _script_target,
    ...skill
  } = detail;
  return skill;
}

function nowMinus(minutes: number): string {
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

const mockState: {
  skills: SkillDetail[];
  recipes: Recipe[];
  runs: Record<string, SkillRun[]>;
} = {
  skills: [
    {
      id: "gmail-skill",
      name: "gmail-skill",
      description: "Gmail에서 메일을 검색하고 읽는 스킬",
      enabled: true,
      source: "global",
      directory: "~/.agents/skills/gmail-skill",
      script_target: "run.py",
      argument_hint: "검색어 또는 메일 ID",
      user_invocable: true,
      retry_policy: {
        max_attempts: 3,
        backoff_seconds: 2,
        backoff_strategy: "exponential",
      },
      last_run: {
        started_at: nowMinus(12),
        status: "ok",
        duration_ms: 1820,
      },
      skill_md:
        "# gmail-skill\n\nGmail에서 메일을 검색하고 읽는 스킬\n\n## Usage\n\n```bash\npython run.py search --query \"from:boss subject:urgent\"\npython run.py read --id MESSAGE_ID\n```\n\n## Trigger\n\n사용자가 메일 확인, 이메일 검색, 읽지 않은 메일 등을 요청할 때\n",
    },
    {
      id: "google-calendar-skill",
      name: "google-calendar-skill",
      description: "Google 캘린더 일정을 조회·생성한다.",
      enabled: true,
      source: "global",
      directory: "~/.agents/skills/google-calendar-skill",
      script_target: "run.py",
      argument_hint: "날짜 또는 이벤트 제목",
      user_invocable: true,
      retry_policy: {
        max_attempts: 2,
        backoff_seconds: 5,
        backoff_strategy: "fixed",
      },
      last_run: {
        started_at: nowMinus(58),
        status: "ok",
        duration_ms: 940,
      },
      skill_md:
        "# google-calendar-skill\n\nGoogle 캘린더 일정을 조회·생성한다.\n\n## Trigger\n\n사용자가 일정·캘린더·미팅 관련 질문을 할 때\n",
    },
    {
      id: "naver-shopping-skill",
      name: "naver-shopping-skill",
      description: "네이버 쇼핑 상품과 가격을 조회한다.",
      enabled: false,
      source: "global",
      directory: "~/.agents/skills/naver-shopping-skill",
      script_target: "run.py",
      argument_hint: "검색어",
      user_invocable: false,
      retry_policy: {
        max_attempts: 1,
        backoff_seconds: 0,
        backoff_strategy: "none",
      },
      last_run: {
        started_at: nowMinus(60 * 24 * 2),
        status: "error",
        duration_ms: 8200,
        error: "rate-limit",
      },
      skill_md:
        "# naver-shopping-skill\n\n네이버 쇼핑 상품과 가격을 조회한다.\n",
    },
    {
      id: "us-stock-skill",
      name: "us-stock-skill",
      description: "미국 주식 시세·뉴스·기본정보를 조회한다.",
      enabled: true,
      source: "global",
      directory: "~/.agents/skills/us-stock-skill",
      script_target: "run.py",
      argument_hint: "티커 (예: AAPL)",
      user_invocable: true,
      retry_policy: {
        max_attempts: 3,
        backoff_seconds: 1,
        backoff_strategy: "exponential",
      },
      last_run: {
        started_at: nowMinus(180),
        status: "ok",
        duration_ms: 2200,
      },
      skill_md:
        "# us-stock-skill\n\n미국 주식 시세·뉴스·기본정보를 조회한다.\n",
    },
    {
      id: "news-search-skill",
      name: "news-search-skill",
      description: "Gemini 그라운딩으로 최신 뉴스를 검색·요약한다.",
      enabled: true,
      source: "global",
      directory: "~/.agents/skills/news-search-skill",
      script_target: "run.py",
      argument_hint: "검색 키워드",
      user_invocable: true,
      retry_policy: {
        max_attempts: 2,
        backoff_seconds: 3,
        backoff_strategy: "exponential",
      },
      last_run: null,
      skill_md:
        "# news-search-skill\n\nGemini API로 최신 뉴스를 검색하고 요약한다.\n",
    },
  ],
  recipes: [
    {
      id: "morning-briefing",
      name: "morning-briefing",
      description: "아침 브리핑 - 메일과 캘린더 요약",
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
      timeout_seconds: 120,
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
      timeout_seconds: 90,
    },
  ],
  runs: {
    "gmail-skill": Array.from({ length: 38 }).map((_, i) => ({
      id: `gmail-${i}`,
      started_at: nowMinus(i * 30 + 12),
      duration_ms: 800 + ((i * 137) % 4000),
      status: i % 11 === 0 ? "error" : i % 7 === 0 ? "timeout" : "ok",
      command:
        i % 5 === 0
          ? "python run.py read --id MSG_AB12"
          : "python run.py search --query is:unread",
      exit_code: i % 11 === 0 ? 1 : 0,
      attempt: i % 11 === 0 ? 2 : 1,
      error: i % 11 === 0 ? "permission denied: token expired" : undefined,
    })),
    "google-calendar-skill": Array.from({ length: 24 }).map((_, i) => ({
      id: `cal-${i}`,
      started_at: nowMinus(i * 60 + 58),
      duration_ms: 600 + ((i * 91) % 1500),
      status: "ok",
      command: "python run.py list --range today",
      exit_code: 0,
      attempt: 1,
    })),
    "us-stock-skill": Array.from({ length: 20 }).map((_, i) => ({
      id: `stock-${i}`,
      started_at: nowMinus(i * 180 + 180),
      duration_ms: 1500 + ((i * 71) % 2200),
      status: i === 5 ? "error" : "ok",
      command: "python run.py quote --ticker AAPL",
      exit_code: i === 5 ? 1 : 0,
      attempt: 1,
      error: i === 5 ? "API quota exceeded" : undefined,
    })),
    "naver-shopping-skill": [
      {
        id: "naver-0",
        started_at: nowMinus(60 * 24 * 2),
        duration_ms: 8200,
        status: "error",
        command: "python run.py search --query 노트북",
        exit_code: 429,
        attempt: 1,
        error: "rate-limit",
      },
    ],
    "news-search-skill": [],
  },
};
