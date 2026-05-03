/**
 * Insight Suggestion Queue API 클라이언트 (BIZ-79).
 *
 * Memory 화면의 운영자 검수 패널이 사용한다. 4종 액션이 모두 admin proxy
 * (`/api/admin/...`) 를 거쳐 Python `/admin/v1/memory/insights/suggestions/*` 로
 * forward 된다 — admin 토큰은 서버 측에서만 합쳐진다.
 *
 * 큐 의미 (BIZ-79):
 * - dreaming 결과는 곧바로 USER.md 에 가지 않고 큐에 pending 으로 적재된다
 * - accept → sidecar 등재 → 다음 dreaming 사이클이 USER.md 로 합산
 * - reject → blocklist 등재 → 다음 사이클이 같은 topic 을 추출하지 않음
 * - edit → 본문만 갱신, status 는 pending 유지
 */

import { fetchAdmin } from "./fetch-admin";

export interface SuggestionItem {
  topic: string;
  text: string;
  evidence_count: number;
  confidence: number;
  source_msg_ids: number[];
  start_msg_id: number | null;
  end_msg_id: number | null;
  status: "pending" | "accepted" | "rejected";
  suggested_at: string;
  first_seen: string;
  last_seen: string;
}

export interface ListSuggestionsResponse {
  items: SuggestionItem[];
  total: number;
}

const BASE = "/api/admin/memory/insights/suggestions";

export function listSuggestions(): Promise<ListSuggestionsResponse> {
  return fetchAdmin<ListSuggestionsResponse>(BASE);
}

export function acceptSuggestion(
  topic: string,
): Promise<{ ok: true; topic: string; promoted: boolean }> {
  return fetchAdmin(`${BASE}/${encodeURIComponent(topic)}/accept`, {
    method: "POST",
  });
}

export function editSuggestion(
  topic: string,
  text: string,
): Promise<{ ok: true; topic: string; item: SuggestionItem }> {
  return fetchAdmin(`${BASE}/${encodeURIComponent(topic)}/edit`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

export function rejectSuggestion(
  topic: string,
  reason?: string,
): Promise<{ ok: true; topic: string; blocked: boolean }> {
  return fetchAdmin(`${BASE}/${encodeURIComponent(topic)}/reject`, {
    method: "POST",
    body: JSON.stringify({ reason: reason ?? "user_rejected" }),
  });
}
