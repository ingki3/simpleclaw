/**
 * Insights / Blocklist API 래퍼 — ``/admin/v1/memory/insights*`` (BIZ-92).
 *
 * `/memory/insights` 페이지의 Active / Archive / Blocklist 탭이 사용한다.
 *
 *  - ``listInsights``  — InsightStore sidecar listing (status=active|archived|all).
 *  - ``listBlocklist`` — BlocklistStore listing (정규형 topic + 사유 + 차단 시각).
 *
 * 설계 결정:
 *  - suggestions.ts 와 동일한 ``fetchAdmin`` 패턴 — base URL 은 same-origin
 *    프록시(`/api/admin`) 가 알아서 처리한다.
 *  - 응답 envelope (active_count / archived_count) 은 그대로 노출 — UI 가
 *    탭 카운트 배지에 사용한다.
 */

import { fetchAdmin } from "./client";

// ---------------------------------------------------------------------------
// 타입 — admin_api._serialize_insight 와 1:1
// ---------------------------------------------------------------------------

/**
 * InsightStore sidecar 한 행. Active 와 Archive 모두 같은 모델을 쓰며,
 * ``archived_at`` 의 null 여부로 분리된다.
 */
export interface InsightItem {
  topic: string;
  text: string;
  evidence_count: number;
  /** 0..1. ≥0.7 = green, 0.4–0.7 = amber, <0.4 = red (BIZ-90 합격 기준 1). */
  confidence: number;
  /** ISO 8601. UI 의 카드 메타 라인. */
  first_seen: string;
  last_seen: string;
  start_msg_id: string | null;
  end_msg_id: string | null;
  source_msg_ids: string[];
  /** ISO 8601 — null=Active, 값있음=Archive. */
  archived_at: string | null;
}

export interface ListInsightsResponse {
  insights: InsightItem[];
  /** 응답 필터에 매칭된 건수. */
  total: number;
  /** sidecar 전체에서 archived_at=null 인 건수 — 탭 배지. */
  active_count: number;
  archived_count: number;
}

export type InsightListStatus = "active" | "archived" | "all";

/**
 * 인사이트 목록 조회. status 미지정 시 active.
 *
 * 경로: ``GET /admin/v1/memory/insights?status=...``.
 */
export function listInsights(
  status?: InsightListStatus,
): Promise<ListInsightsResponse> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return fetchAdmin<ListInsightsResponse>(`/memory/insights${query}`);
}

// ---------------------------------------------------------------------------
// Blocklist
// ---------------------------------------------------------------------------

/**
 * BlocklistStore (BIZ-79) 한 행. ``topic`` 은 사용자 표시용 원문, ``topic_key``
 * 는 정규화된 키 (재학습 차단 매칭에 사용).
 */
export interface BlocklistEntry {
  topic: string;
  topic_key: string;
  reason: string;
  /** ISO 8601. */
  blocked_at: string | null;
}

export interface ListBlocklistResponse {
  entries: BlocklistEntry[];
  total: number;
}

/** 차단 토픽 목록. blocked_at 내림차순 (백엔드 정렬). */
export function listBlocklist(): Promise<ListBlocklistResponse> {
  return fetchAdmin<ListBlocklistResponse>("/memory/blocklist");
}
