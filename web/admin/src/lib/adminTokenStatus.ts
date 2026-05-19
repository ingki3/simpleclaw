/**
 * ``ADMIN_API_TOKEN`` 정합성 상태를 추적하는 서버 측 모듈 (BIZ-245).
 *
 * BIZ-244 사고 재발 방지 — vault 만 회전되고 ``.env.local`` 의 ``ADMIN_API_TOKEN``
 * 이 stale 인 상태로 방치되면 프록시(``/api/admin/[...path]``)가 옛 Bearer 토큰을
 * 계속 forward 해 모든 admin UI 패널이 401 로 빈 상태가 된다. 본 모듈은 부팅 시
 * 1회 헬스 체크 결과를 보관하고, 프록시에서 발생한 401 도 같은 사실(=mismatch)로
 * 기록해 운영자가 ``npm run dev`` 콘솔 및 (후속) 배너에서 즉시 감지하도록 한다.
 *
 * 설계 결정:
 *
 * - **모듈 싱글톤**: Next 서버 프로세스 1개당 1개의 상태. 인스ュ런테이션 훅과
 *   프록시 라우트가 같은 객체를 공유한다.
 * - **로그는 1회**: 같은 미스매치 사실을 401 매 호출마다 찍으면 콘솔 노이즈가 커진다.
 *   상태 전이 시점(``ok`` → ``mismatch``)에서만 한 번 경고를 남긴다.
 * - **상태값을 4분류로 분리**: ``unchecked`` / ``ok`` / ``mismatch`` / ``unreachable``
 *   / ``missing_token``. 후속 ``/api/admin/_token_status`` 엔드포인트 + UI 배너가
 *   각 상태별로 다른 메시지를 표시할 수 있도록 한다.
 */

export type AdminTokenStatus =
  | "unchecked"
  | "ok"
  | "mismatch"
  | "unreachable"
  | "missing_token";

export interface AdminTokenState {
  status: AdminTokenStatus;
  checkedAt: number | null;
  detail: string | null;
}

interface StatusStore {
  state: AdminTokenState;
  warned: boolean;
}

// 모듈 평가 시 1회만 만들어지는 객체. ES 모듈은 import 캐시되므로 instrumentation
// 과 route.ts 가 같은 인스턴스를 공유한다.
const store: StatusStore = {
  state: { status: "unchecked", checkedAt: null, detail: null },
  warned: false,
};

function logMismatchOnce(detail: string): void {
  if (store.warned) return;
  store.warned = true;
  // 운영자가 ``npm run dev`` 콘솔에서 즉시 인지할 수 있도록 prefix 와 가이드를 동봉.
  console.warn(
    `[admin][token-mismatch] ADMIN_API_TOKEN 이 데몬의 admin_api_token 과 일치하지 않습니다 — ${detail}\n` +
      `  → 데몬 재기동(.venv/bin/python scripts/run_bot.py)/회전 후 \`scripts/setup_admin_api.py\` 를 다시 실행하거나,\n` +
      `    web/admin/.env.local 의 ADMIN_API_TOKEN 라인을 새 토큰으로 교체한 뒤 \`npm run dev\` 재기동하세요.`,
  );
}

function logRecoveryOnce(): void {
  if (!store.warned) return;
  // 한 번 경고했던 상태에서 회복되면 같은 경고가 또 나오지 않도록 ``warned`` 를 리셋
  // 한다 — 이후 또 미스매치가 발생하면 다시 한 번 경고한다.
  store.warned = false;
  console.info(
    "[admin][token-mismatch] 토큰이 다시 데몬과 일치합니다 — 정상 복귀.",
  );
}

export function getAdminTokenState(): AdminTokenState {
  return { ...store.state };
}

export function markTokenChecked(detail = "boot-time health check 200"): void {
  const prev = store.state.status;
  store.state = { status: "ok", checkedAt: Date.now(), detail };
  if (prev === "mismatch") {
    logRecoveryOnce();
  }
}

export function markTokenMismatch(detail: string): void {
  store.state = { status: "mismatch", checkedAt: Date.now(), detail };
  logMismatchOnce(detail);
}

export function markTokenMissing(): void {
  if (store.state.status === "missing_token") return;
  store.state = {
    status: "missing_token",
    checkedAt: Date.now(),
    detail:
      "ADMIN_API_TOKEN 이 비어 있습니다 — web/admin/.env.local 또는 시스템 환경변수에 토큰을 넣어주세요.",
  };
  if (!store.warned) {
    store.warned = true;
    console.warn(
      `[admin][token-missing] ${store.state.detail}`,
    );
  }
}

export function markDaemonUnreachable(detail: string): void {
  // 데몬이 안 떠있는 상황은 토큰 문제와 분리해 표시한다 — 운영자가 다른 액션(데몬 재기동)
  // 을 취해야 하기 때문. 같은 상태로 굳어지면 매 호출마다 다시 찍지 않는다.
  if (store.state.status === "unreachable") return;
  store.state = { status: "unreachable", checkedAt: Date.now(), detail };
  console.warn(`[admin][daemon-unreachable] ${detail}`);
}

/** 테스트 격리용 — 모듈 싱글톤을 초기 상태로 되돌린다. */
export function _resetAdminTokenStateForTests(): void {
  store.state = { status: "unchecked", checkedAt: null, detail: null };
  store.warned = false;
}
