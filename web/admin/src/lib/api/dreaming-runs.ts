/**
 * Dreaming observability API 래퍼 — ``/admin/v1/memory/dreaming/{runs,status}`` (BIZ-81).
 *
 * Memory 화면의 KPI/진단 패널 (DreamingObservabilityPanel) 이 호출한다. suggestions.ts
 * 와 동일한 패턴으로 ``client.fetchAdmin`` 위에 얇게 얹혀 same-origin 프록시
 * (``/api/admin/...``) 를 통과한다 — 토큰 주입은 Next 서버측 라우트가 책임진다.
 *
 * 응답 envelope 은 그대로 노출하고 호출자가 의미를 부여한다. 백엔드가 향후
 * 필드를 추가해도 본 모듈은 영향 없도록 union 타입은 만들지 않는다.
 */

import { fetchAdmin } from "./client";

// ---------------------------------------------------------------------------
// 타입 — admin_api.AdminAPIServer._serialize_dreaming_run 와 1:1.
// ---------------------------------------------------------------------------

/** 한 사이클의 종합 상태. ``running`` 은 begin 직후 finish 전 짧은 윈도우. */
export type DreamingRunStatus = "success" | "skip" | "error" | "running";

/**
 * DreamingPipeline 한 사이클의 측정 결과 — sidecar JSONL 한 행.
 *
 * - ``ended_at`` 이 null 이면 사이클 본문이 끝나지 않은 *running* 상태.
 * - ``status`` 는 백엔드가 (skip_reason / error / ended_at) 으로부터 파생한 값.
 * - ``details`` 는 진단 컨텍스트 — preflight 실패 시 ``{message: "..."}`` 등.
 */
export interface DreamingRun {
  id: string;
  /** ISO 8601. */
  started_at: string;
  /** ISO 8601 — finish 후 채워진다. running 중이면 null. */
  ended_at: string | null;
  /** 끝났다면 (ended_at - started_at) 초. */
  duration_seconds: number | null;
  input_msg_count: number;
  generated_insight_count: number;
  rejected_count: number;
  /** 예외 메시지 — 실패 시에만 채워짐. ``error`` 는 ``skip_reason`` 보다 우선. */
  error: string | null;
  /** "no_messages" / "preflight_failed" / "midwrite_aborted" / "empty_results" 등. */
  skip_reason: string | null;
  status: DreamingRunStatus;
  details: Record<string, unknown>;
}

export interface ListDreamingRunsResponse {
  runs: DreamingRun[];
  total: number;
}

/** 7일 윈도우 KPI 집계 — ``DreamingRunStore.kpi_window`` 의 출력과 동일. */
export interface DreamingKpiWindow {
  window_days: number;
  total_runs: number;
  success: number;
  skip: number;
  error: number;
  input_msg_total: number;
  insight_total: number;
  rejected_total: number;
  /** ``{skip_reason: count}``. */
  skip_breakdown: Record<string, number>;
}

/**
 * 운영자 리뷰 누적 거절률 — suggestion_store 가 비활성이면 ``rate=null``.
 *
 * BIZ-66 §3-K 정의: "거절률 KPI는 H의 Admin Review Loop 신호에서 산출". 즉
 * dreaming 사이클 자체의 ``rejected_count`` (블록리스트로 차단된 토픽) 와는
 * 다른 신호이다. UI 가 두 지표를 별도로 보여줘야 한다.
 */
export interface DreamingRejection {
  reviewed: number;
  rejected: number;
  rate: number | null;
}

/** ``/memory/dreaming/status`` 의 합쳐진 응답 — 패널이 단일 호출로 받아간다. */
export interface DreamingStatusResponse {
  /** 가장 최근 회차 (success/skip/error/running 중 어떤 것이든). */
  last_run: DreamingRun | null;
  /** 가장 최근 *성공* 회차 — UI 가 "마지막으로 USER.md 가 갱신된 시점" 표시에 사용. */
  last_successful_run: DreamingRun | null;
  /** 데몬이 추정한 다음 시도 시각(ISO). 정확하진 않은 best-effort. */
  next_run: string | null;
  /** 현재 설정된 야간 시간(시 0..23). */
  overnight_hour: number | null;
  /** 필요한 사용자 idle 초. */
  idle_threshold_seconds: number | null;
  /** "오늘 이미 1회 실행됨" / "야간 시간 미도래" 등 한 줄짜리 사유. */
  trigger_blockers: string[];
  /** blockers 를 합친 사람용 한 줄 메시지 — 비어 있으면 ``next_run`` 안내. */
  trigger_message: string | null;
  /** 7일 윈도우 KPI. ``metrics_enabled=false`` 면 null. */
  kpi_7d: DreamingKpiWindow | null;
  /** 운영자 리뷰 거절률. */
  rejection: DreamingRejection;
  /** dreaming_run_store 주입 여부 — false 면 last_run/kpi 는 null. */
  metrics_enabled: boolean;
}

// ---------------------------------------------------------------------------
// REST 호출
// ---------------------------------------------------------------------------

/** 최근 N건의 사이클 메트릭(최신순). 기본 20, 상한 200. */
export function listDreamingRuns(
  limit: number = 20,
): Promise<ListDreamingRunsResponse> {
  // 클라이언트도 1..200 으로 클램프 — 백엔드는 폴백하지만 URL 위생을 보강한다.
  const clamped = Math.max(1, Math.min(200, Math.floor(limit)));
  return fetchAdmin<ListDreamingRunsResponse>(
    `/memory/dreaming/runs?limit=${clamped}`,
  );
}

/** 합쳐진 status — last_run + next_run + KPI + rejection + 진단. */
export function getDreamingStatusV2(): Promise<DreamingStatusResponse> {
  return fetchAdmin<DreamingStatusResponse>("/memory/dreaming/status");
}
