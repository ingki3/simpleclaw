/**
 * Cron 화면에서 다루는 도메인 타입.
 *
 * 백엔드(`/admin/v1/cron/*`)가 BIZ-41 후속 작업으로 들어올 예정이라
 * 본 타입은 `src/simpleclaw/daemon/models.py`의 ``CronJob`` /
 * ``CronJobExecution`` 스키마를 그대로 미러링한다. snake_case → camelCase
 * 변환만 담당하고, 시각 표현용 파생 필드(예: ``status.tone``)는 컴포넌트에서
 * 계산한다.
 */

/** 작업 액션 종류 — daemon.models.ActionType과 1:1. */
export type CronActionType = "prompt" | "recipe";

/** 백오프 전략 — daemon.models.BackoffStrategy와 1:1. */
export type CronBackoffStrategy = "linear" | "exponential";

/** 단일 실행의 상태. */
export type CronRunStatus = "running" | "success" | "failed" | "skipped";

export interface CronJob {
  /** 사용자가 부여한 잡 이름(=primary key). */
  name: string;
  /** 표준 5필드 cron 표현식. */
  cronExpression: string;
  actionType: CronActionType;
  /** 액션 참조 — prompt 본문 또는 recipe id. */
  actionReference: string;
  enabled: boolean;
  createdAt: string;
  updatedAt: string;
  maxAttempts: number;
  backoffSeconds: number;
  backoffStrategy: CronBackoffStrategy;
  circuitBreakThreshold: number;
  /** 연속 실패 카운터 — 임계값에 도달하면 백엔드가 자동 비활성화한다. */
  consecutiveFailures: number;
  /** 백엔드가 알고 있는 마지막 결과 — 빠른 행 렌더링을 위해 join해 둔다. */
  lastRun?: CronRun | null;
}

export interface CronRun {
  id: number;
  jobName: string;
  startedAt: string;
  finishedAt?: string | null;
  status: CronRunStatus;
  attempt: number;
  /** 실행 결과 요약 — orchestrator의 응답 첫 N자. */
  resultSummary: string;
  /** 실패 시의 stderr/예외 트레이스 스냅샷. */
  errorDetails: string;
}

/** 새 잡 생성 폼이 백엔드로 전달하는 입력. */
export interface CronJobInput {
  name: string;
  cronExpression: string;
  actionType: CronActionType;
  /** prompt 본문 또는 recipe id. */
  actionReference: string;
  enabled: boolean;
}

/** Run-now 결과 — 토스트 메시지에 사용. */
export interface CronRunNowResult {
  ok: boolean;
  /** 실패 시 사용자에게 노출할 한 줄 메시지. */
  message: string;
  run?: CronRun;
}
