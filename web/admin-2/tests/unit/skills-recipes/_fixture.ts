/**
 * Skills & Recipes 단위 테스트용 공통 픽스처.
 */
import type {
  CatalogSkill,
  InstalledSkill,
  Recipe,
} from "@/app/(shell)/skills-recipes/_data";

export const SKILL: InstalledSkill = {
  id: "gmail-skill",
  name: "gmail-skill",
  description: "Gmail 검색 스킬",
  source: "global",
  userInvocable: true,
  enabled: true,
  directory: "~/.agents/skills/gmail-skill",
  argumentHint: "검색어",
  retryPolicy: {
    maxAttempts: 3,
    backoffSeconds: 2,
    backoffStrategy: "exponential",
    timeoutSeconds: 30,
  },
  lastRun: null,
  health: { tone: "success", label: "정상" },
};

export const RECIPE: Recipe = {
  id: "morning-briefing",
  name: "morning-briefing",
  description: "아침 브리핑",
  enabled: true,
  trigger: "아침 브리핑",
  skills: ["gmail-skill"],
  version: "v2",
  steps: [
    { name: "메일", type: "skill", summary: "gmail 호출" },
    { name: "요약", type: "instruction", summary: "응답 정리" },
  ],
  timeoutSeconds: 120,
};

export const CATALOG: CatalogSkill[] = [
  {
    id: "gmail-skill",
    name: "gmail-skill",
    description: "Gmail 스킬",
    publisher: "simpleclaw",
    source: "global",
    userInvocable: true,
    keywords: ["gmail", "메일"],
    category: "생산성",
    installed: true,
  },
  {
    id: "weather-skill",
    name: "weather-skill",
    description: "날씨 조회",
    publisher: "community",
    source: "global",
    userInvocable: true,
    keywords: ["weather", "날씨"],
    category: "정보",
    installed: false,
  },
];
