/**
 * Cron API 클라이언트.
 *
 * `/admin/v1/cron/*` 백엔드 엔드포인트는 BIZ-41 후속 작업으로 들어올 예정이라,
 * 본 모듈은 동일한 시그니처의 **메모리 기반 mock**으로 화면을 구동한다. 실제
 * 백엔드가 준비되면 이 모듈만 ``fetchAdmin``(BIZ-43) 기반 구현으로 교체하고
 * 호출 측은 변경하지 않는다.
 *
 * 메모리 mock은 모듈 단위 클로저로 보관해 SPA 라이프사이클(다음 새로고침까지)
 * 동안만 유지된다. 영속화는 의도적으로 생략 — 실제 데이터는 데몬 SQLite가
 * 보관할 책임이라 UI 단에 백업 저장소를 두지 않는다.
 */

import type {
  CronJob,
  CronJobInput,
  CronRun,
  CronRunNowResult,
} from "./types";
import { getNextRuns, validateCronExpression } from "./expression";

/** Run-now가 만들 ``CronRun.id`` 시퀀스. */
let runIdSeq = 1000;

/** mock 잡 — 비어 있는 상태(empty UI) 검증용으로 의도적으로 1~2건만 둔다. */
const seedJobs: CronJob[] = [
  {
    name: "morning-brief",
    cronExpression: "0 9 * * *",
    actionType: "prompt",
    actionReference: "오늘 일정과 미해결 메시지를 요약해 줘.",
    enabled: true,
    createdAt: "2026-04-20T09:00:00+09:00",
    updatedAt: "2026-04-30T08:12:33+09:00",
    maxAttempts: 3,
    backoffSeconds: 60,
    backoffStrategy: "exponential",
    circuitBreakThreshold: 5,
    consecutiveFailures: 0,
    lastRun: {
      id: 901,
      jobName: "morning-brief",
      startedAt: "2026-05-03T09:00:01+09:00",
      finishedAt: "2026-05-03T09:00:08+09:00",
      status: "success",
      attempt: 1,
      resultSummary: "모닝 브리핑 전송 완료 — 새 일정 3건, 미답 메시지 1건.",
      errorDetails: "",
    },
  },
  {
    name: "weekly-digest",
    cronExpression: "0 18 * * 5",
    actionType: "recipe",
    actionReference: "weekly_digest",
    enabled: false,
    createdAt: "2026-03-11T18:00:00+09:00",
    updatedAt: "2026-04-29T18:00:00+09:00",
    maxAttempts: 3,
    backoffSeconds: 120,
    backoffStrategy: "exponential",
    circuitBreakThreshold: 5,
    consecutiveFailures: 3,
    lastRun: {
      id: 887,
      jobName: "weekly-digest",
      startedAt: "2026-05-01T18:00:00+09:00",
      finishedAt: "2026-05-01T18:00:12+09:00",
      status: "failed",
      attempt: 3,
      resultSummary: "",
      errorDetails:
        "RecipeError: step 'compose-digest' 실패 — LLM 응답 timeout (12s).",
    },
  },
];

const jobs = new Map<string, CronJob>(seedJobs.map((j) => [j.name, j]));

/** 실행 이력 — 잡 이름별 최근 실행을 시간순(최신이 앞)으로 보관. */
const runs = new Map<string, CronRun[]>();
for (const job of seedJobs) {
  const initial: CronRun[] = job.lastRun ? [job.lastRun] : [];
  // 데모용 과거 실행 — 빈 Drawer를 피하면서 UI 패턴을 보여준다.
  if (job.name === "morning-brief") {
    initial.push(
      run(902, "morning-brief", "2026-05-02T09:00:01+09:00", "success",
        "모닝 브리핑 전송 완료."),
      run(903, "morning-brief", "2026-05-01T09:00:01+09:00", "success",
        "모닝 브리핑 전송 완료."),
    );
  }
  runs.set(job.name, initial);
}

function run(
  id: number,
  name: string,
  startedAt: string,
  status: CronRun["status"],
  summary = "",
  err = "",
): CronRun {
  return {
    id,
    jobName: name,
    startedAt,
    finishedAt: startedAt,
    status,
    attempt: 1,
    resultSummary: summary,
    errorDetails: err,
  };
}

/** 외부 호출에 약간의 비동기성을 남겨 실제 fetch 흐름과 동일한 패턴으로 둔다. */
function delay<T>(value: T, ms = 120): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

/** ``GET /admin/v1/cron/jobs`` */
export async function listCronJobs(): Promise<CronJob[]> {
  return delay([...jobs.values()].sort((a, b) => a.name.localeCompare(b.name)));
}

/** ``GET /admin/v1/cron/jobs/{name}/runs?limit=20`` */
export async function listCronRuns(jobName: string, limit = 20): Promise<CronRun[]> {
  const list = runs.get(jobName) ?? [];
  return delay(list.slice(0, limit));
}

/** ``POST /admin/v1/cron/jobs`` */
export async function createCronJob(input: CronJobInput): Promise<CronJob> {
  if (jobs.has(input.name)) {
    throw new Error(`이미 같은 이름의 잡이 있어요: ${input.name}`);
  }
  const validation = validateCronExpression(input.cronExpression);
  if (!validation.valid) {
    throw new Error(validation.error);
  }
  const now = new Date().toISOString();
  const job: CronJob = {
    name: input.name,
    cronExpression: input.cronExpression,
    actionType: input.actionType,
    actionReference: input.actionReference,
    enabled: input.enabled,
    createdAt: now,
    updatedAt: now,
    maxAttempts: 3,
    backoffSeconds: 60,
    backoffStrategy: "exponential",
    circuitBreakThreshold: 5,
    consecutiveFailures: 0,
    lastRun: null,
  };
  jobs.set(job.name, job);
  runs.set(job.name, []);
  return delay(job);
}

/** ``PATCH /admin/v1/cron/jobs/{name}`` — enabled 토글 등 부분 갱신. */
export async function updateCronJob(
  name: string,
  patch: Partial<Pick<CronJob, "enabled" | "cronExpression">>,
): Promise<CronJob> {
  const job = jobs.get(name);
  if (!job) throw new Error(`잡을 찾을 수 없어요: ${name}`);
  const next: CronJob = {
    ...job,
    ...patch,
    updatedAt: new Date().toISOString(),
  };
  jobs.set(name, next);
  return delay(next);
}

/** ``DELETE /admin/v1/cron/jobs/{name}`` */
export async function deleteCronJob(name: string): Promise<void> {
  jobs.delete(name);
  runs.delete(name);
  return delay(undefined);
}

/**
 * ``POST /admin/v1/cron/jobs/{name}/run-now``
 *
 * mock은 항상 성공한 척 한다 — 실제 결과는 백엔드 통합 시 ``CronRun``으로
 * 채워진다. 실패 분기를 보고 싶다면 ``failing-now`` 이름으로 잡을 만들어
 * 테스트한다(이름에 ``fail``이 들어가면 의도적으로 실패).
 */
export async function runCronJobNow(name: string): Promise<CronRunNowResult> {
  const job = jobs.get(name);
  if (!job) {
    return { ok: false, message: `잡을 찾을 수 없어요: ${name}` };
  }
  const failing = name.toLowerCase().includes("fail");
  const startedAt = new Date().toISOString();
  const newRun: CronRun = {
    id: ++runIdSeq,
    jobName: name,
    startedAt,
    finishedAt: startedAt,
    status: failing ? "failed" : "success",
    attempt: 1,
    resultSummary: failing ? "" : "수동 실행 성공.",
    errorDetails: failing ? "수동 실행 실패 — mock에서 의도적으로 throw." : "",
  };
  const history = runs.get(name) ?? [];
  history.unshift(newRun);
  runs.set(name, history);
  jobs.set(name, { ...job, lastRun: newRun });
  return delay({
    ok: !failing,
    message: failing
      ? `잡 '${name}' 수동 실행 실패 — 자세한 내용은 이력에서 확인해 주세요.`
      : `잡 '${name}' 수동 실행 완료.`,
    run: newRun,
  });
}

/**
 * ``POST /admin/v1/cron/preview``
 *
 * 표현식 유효성 + 다음 5회 예상 실행 시각을 반환한다. 서버가 권위적이지만
 * 클라이언트 검증과 동일 알고리즘이라 응답 구조만 미러링한다.
 */
export async function previewCronExpression(
  expr: string,
  count = 5,
): Promise<
  | { valid: true; description: string; nextRuns: string[] }
  | { valid: false; error: string }
> {
  const result = validateCronExpression(expr);
  if (!result.valid) return delay({ valid: false, error: result.error });
  const next = getNextRuns(expr, count).map((d) => d.toISOString());
  return delay({ valid: true, description: result.description, nextRuns: next });
}
