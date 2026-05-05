/**
 * Suggestions API 래퍼 — ``/admin/v1/memory/suggestions/*`` (BIZ-79).
 *
 * 드리밍 dry-run 큐를 운영자가 검토하기 위한 클라이언트 어댑터.
 *
 *  - ``listSuggestions``        — pending(기본) 또는 status 필터로 큐 조회.
 *  - ``getSuggestionSources``   — 한 건의 근거 메시지(BIZ-77)를 함께 반환.
 *  - ``acceptSuggestion``       — 원문 그대로 USER.md 에 적용.
 *  - ``editSuggestion``         — body.text 로 치환 후 적용.
 *  - ``rejectSuggestion``       — 블록리스트(BIZ-78 stub)에 추가하고 큐에서 제거.
 *
 * 설계 결정:
 *  - secrets/channels 와 동일하게 ``client.ts``의 ``fetchAdmin`` 위에 얇게 얹는다.
 *    프록시 라우트(``/api/admin/[...path]``)가 토큰을 주입하므로 클라이언트는
 *    base URL 을 ``/api/admin``으로만 안다.
 *  - 응답 envelope 은 그대로 통과시키고 호출자(컴포넌트)가 의미를 부여한다 —
 *    포괄 래퍼를 만들지 않아 future 필드 추가에 영향 받지 않는다.
 */

import { fetchAdmin } from "./client";

// ---------------------------------------------------------------------------
// 타입 — admin_api.AdminAPIServer._serialize_suggestion 와 1:1 매핑.
// ---------------------------------------------------------------------------

/** Pending 큐의 한 행 — 운영자 검토 대상. terminal status 는 디버깅 시에만 노출. */
export type SuggestionStatus = "pending" | "accepted" | "edited" | "rejected";

export interface Suggestion {
  id: string;
  topic: string;
  /** dreaming 이 추출한 원문(insight bullet). */
  text: string;
  /** edit 으로 치환된 경우 보존된다. accept/reject 면 null. */
  edited_text: string | null;
  /** edited 이면 edited_text, 그 외엔 text — UI 표시용 단축 필드. */
  applied_text: string;
  /** 0..1 — auto-promote 임계값(기본 0.7) 비교에 사용. */
  confidence: number;
  /** 누적 관찰 횟수 — auto-promote 임계값(기본 3) 비교에 사용. */
  evidence_count: number;
  /** 근거가 된 message id 들. */
  source_msg_ids: string[];
  start_msg_id: string | null;
  end_msg_id: string | null;
  status: SuggestionStatus;
  reject_reason: string | null;
  /** ISO 8601. */
  created_at: string;
  /** ISO 8601 — pending 시 마지막 evidence 보강 시각. */
  updated_at: string;
}

export interface ListSuggestionsResponse {
  suggestions: Suggestion[];
  total: number;
  /** 전체 큐에서 status=pending 인 건수 — 필터와 무관하게 헤더 배지에 노출. */
  pending_count: number;
}

/** 근거 메시지 한 건 — BIZ-77 source linkage 가 반환하는 형태. */
export interface SuggestionSourceMessage {
  id: string;
  /** ConversationStore 의 Role enum 값 — ``user`` / ``assistant`` / ``system`` / ``tool`` 등. */
  role: string;
  content: string;
  /** ISO 8601. */
  timestamp: string;
  channel: string | null;
  token_count: number | null;
}

export interface SuggestionSourcesResponse {
  suggestion: Suggestion;
  sources: SuggestionSourceMessage[];
}

// ---------------------------------------------------------------------------
// REST 호출
// ---------------------------------------------------------------------------

/**
 * 큐 조회. ``status`` 미지정 시 pending 만 — 운영자 워크플로우의 기본값.
 * 디버깅·감사 용도에는 ``"all"`` 을 명시한다.
 */
export function listSuggestions(
  status?: SuggestionStatus | "all",
): Promise<ListSuggestionsResponse> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return fetchAdmin<ListSuggestionsResponse>(`/memory/suggestions${query}`);
}

/** 한 건의 근거 메시지를 함께 받아온다 — 모달 "근거 보기" 진입점. */
export function getSuggestionSources(
  id: string,
): Promise<SuggestionSourcesResponse> {
  return fetchAdmin<SuggestionSourcesResponse>(
    `/memory/suggestions/${encodeURIComponent(id)}/sources`,
  );
}

/** 원문 그대로 USER.md 에 append. 멱등키는 client.ts 가 자동 부여. */
export function acceptSuggestion(id: string): Promise<Suggestion> {
  return fetchAdmin<Suggestion>(
    `/memory/suggestions/${encodeURIComponent(id)}/accept`,
    { method: "POST", json: {} },
  );
}

/** 운영자가 수정한 텍스트로 USER.md 에 append. 빈 문자열은 422. */
export function editSuggestion(id: string, text: string): Promise<Suggestion> {
  return fetchAdmin<Suggestion>(
    `/memory/suggestions/${encodeURIComponent(id)}/edit`,
    { method: "POST", json: { text } },
  );
}

/** BIZ-93: blocklist 차단 기간 — 운영자가 모달에서 단일 선택. null 은 영구. */
export type RejectBlocklistPeriodDays = 30 | 90 | 180 | null;

export interface RejectSuggestionOptions {
  /** audit 만 위한 자유서술. 빈 문자열도 허용. */
  reason?: string;
  /** 30/90/180/null — 미지정 시 backend 가 영구로 처리. */
  blocklist_period_days?: RejectBlocklistPeriodDays;
}

/**
 * 블록리스트에 topic 을 추가하고 큐에서 제거.
 *
 * BIZ-93 으로 옵션 객체 인자가 도입됐다. 기존 ``string`` 시그니처(reason only)
 * 도 호환을 위해 유지 — 내부에서 normalize.
 */
export function rejectSuggestion(
  id: string,
  optsOrReason?: string | RejectSuggestionOptions,
): Promise<Suggestion> {
  const opts: RejectSuggestionOptions =
    typeof optsOrReason === "string"
      ? { reason: optsOrReason }
      : (optsOrReason ?? {});

  const body: Record<string, unknown> = { reason: opts.reason ?? "" };
  // ``blocklist_period_days`` 가 명시된 경우만 페이로드에 포함 — undefined 는
  // 전달하지 않아 backend 가 기본값(영구)을 쓰도록 한다. null 은 운영자가
  // 영구 차단을 명시 선택한 경우이므로 그대로 전송.
  if (opts.blocklist_period_days !== undefined) {
    body.blocklist_period_days = opts.blocklist_period_days;
  }

  return fetchAdmin<Suggestion>(
    `/memory/suggestions/${encodeURIComponent(id)}/reject`,
    { method: "POST", json: body },
  );
}
