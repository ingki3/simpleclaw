/**
 * ``adminTokenStatus`` 모듈 단위 테스트 (BIZ-245).
 *
 * 부팅 훅(instrumentation.ts)과 프록시 라우트가 같은 모듈 싱글톤을 통해 상태를
 * 공유한다. 미스매치 → ok 회복 → 다시 미스매치 같은 상태 전이에서 콘솔 경고가
 * 의도대로 1회씩만 나오는지가 핵심.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  _resetAdminTokenStateForTests,
  getAdminTokenState,
  markDaemonUnreachable,
  markTokenChecked,
  markTokenMismatch,
  markTokenMissing,
} from "../adminTokenStatus";

describe("adminTokenStatus", () => {
  let warn: ReturnType<typeof vi.spyOn>;
  let info: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    _resetAdminTokenStateForTests();
    warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    info = vi.spyOn(console, "info").mockImplementation(() => {});
  });

  afterEach(() => {
    warn.mockRestore();
    info.mockRestore();
  });

  it("initial state is unchecked", () => {
    expect(getAdminTokenState().status).toBe("unchecked");
  });

  it("markTokenMismatch logs warning once even on repeated calls", () => {
    markTokenMismatch("GET /health → 401");
    markTokenMismatch("GET /health → 401");
    markTokenMismatch("PATCH /config → 401");

    expect(getAdminTokenState().status).toBe("mismatch");
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn.mock.calls[0][0]).toContain("token-mismatch");
  });

  it("markTokenChecked clears warning state and logs recovery once", () => {
    markTokenMismatch("first 401");
    expect(warn).toHaveBeenCalledTimes(1);

    markTokenChecked();
    expect(getAdminTokenState().status).toBe("ok");
    expect(info).toHaveBeenCalledTimes(1);

    // 회복 후 다시 미스매치가 나면 또 1회 경고를 내야 한다.
    markTokenMismatch("second 401");
    expect(warn).toHaveBeenCalledTimes(2);
  });

  it("markTokenMissing logs once and stays in missing_token state", () => {
    markTokenMissing();
    markTokenMissing();
    expect(getAdminTokenState().status).toBe("missing_token");
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn.mock.calls[0][0]).toContain("token-missing");
  });

  it("markDaemonUnreachable is separate from token mismatch", () => {
    markDaemonUnreachable("ECONNREFUSED");
    expect(getAdminTokenState().status).toBe("unreachable");
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn.mock.calls[0][0]).toContain("daemon-unreachable");

    // 같은 상태로 굳어진 경우 노이즈 방지: 두 번째 호출은 무시.
    markDaemonUnreachable("ECONNREFUSED");
    expect(warn).toHaveBeenCalledTimes(1);
  });
});
