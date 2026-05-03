/**
 * 설정 키 인덱스 — CommandPalette ⌘K 검색의 2차 결과(DESIGN.md §4.8).
 *
 * 데몬의 ``config.yaml`` 키 중 *운영자가 자주 검색하는 것*을 1차 인벤토리로 추렸다.
 * 영역(area)은 admin_api.py의 ``AREA_TO_YAML_KEY`` 키와 매핑되며, 라우팅 시
 * ``/{area}`` 페이지로 이동 + ``?focus=<key>`` 쿼리로 해당 row를 강조한다.
 *
 * 본 목록은 후속 이슈에서 ``GET /admin/v1/config`` 응답을 동적으로 평탄화하는 방식으로
 * 대체될 수 있다. 그때까지는 본 정적 인덱스가 ⌘K 가시성을 빠르게 확보한다.
 */

import type { AdminArea } from "@/lib/api";

export interface SettingKey {
  /** dotted key — ``llm.providers.claude.model``. */
  key: string;
  /** 사용자에게 보일 짧은 설명. */
  label: string;
  area: AdminArea;
}

export const SETTING_KEYS: ReadonlyArray<SettingKey> = [
  // LLM
  { key: "llm.router.primary", label: "기본 프로바이더", area: "llm" },
  { key: "llm.router.fallback", label: "폴백 순서", area: "llm" },
  {
    key: "llm.providers.claude.model",
    label: "Claude 기본 모델",
    area: "llm",
  },
  {
    key: "llm.providers.openai.model",
    label: "OpenAI 기본 모델",
    area: "llm",
  },
  {
    key: "llm.providers.gemini.model",
    label: "Gemini 기본 모델",
    area: "llm",
  },
  // Webhook / Channels
  { key: "webhook.rate_limit", label: "Webhook RPS", area: "webhook" },
  { key: "webhook.body_limit", label: "Webhook 본문 한도", area: "webhook" },
  {
    key: "telegram.allowed_chats",
    label: "Telegram 허용 채팅",
    area: "telegram",
  },
  // Memory
  { key: "memory.enable_dreaming", label: "드리밍 활성", area: "memory" },
  { key: "memory.token_budget", label: "메모리 토큰 예산", area: "memory" },
  // Security
  { key: "security.command_guard", label: "Command Guard", area: "security" },
  // Skills
  { key: "skills.retry_max_attempts", label: "스킬 재시도 횟수", area: "skills" },
  // Daemon / Cron
  { key: "daemon.host", label: "데몬 host", area: "system" },
  { key: "daemon.port", label: "데몬 port", area: "system" },
  { key: "daemon.cron_retry.max_attempts", label: "Cron 재시도", area: "cron" },
  // Persona
  { key: "persona.token_budget", label: "페르소나 토큰 예산", area: "persona" },
];

/**
 * 영역 라우트 매핑 — ``AdminArea`` → 사이드바 라우트.
 *
 * ⌘K에서 설정 키를 선택했을 때 어떤 페이지로 이동시킬지 결정한다.
 * 사이드바는 11개 라우트(BIZ-40)지만 백엔드 영역은 더 세분화돼 있어 일부는
 * 같은 페이지로 묶인다.
 */
export const AREA_TO_ROUTE: Record<AdminArea, string> = {
  llm: "/llm",
  agent: "/system",
  memory: "/memory",
  security: "/system",
  skills: "/skills",
  mcp: "/skills",
  voice: "/channels",
  telegram: "/channels",
  webhook: "/channels",
  channels: "/channels",
  sub_agents: "/system",
  daemon: "/system",
  cron: "/cron",
  persona: "/persona",
  system: "/system",
};
