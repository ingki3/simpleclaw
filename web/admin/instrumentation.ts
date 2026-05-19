/**
 * Next 부팅 훅 — 토큰 정합성 self-check (BIZ-245).
 *
 * Next 16 의 `instrumentation.ts` 는 서버 프로세스가 뜰 때 1회 호출된다. 본 훅은
 * 부팅 직후 데몬 ``/admin/v1/health`` 를 ``ADMIN_API_TOKEN`` 으로 한 번 두드려
 * 401(=토큰 불일치) / 502(=데몬 미기동) 이면 즉시 콘솔에 명시적 경고를 남긴다.
 *
 * BIZ-244 사고 — vault 만 회전되고 ``.env.local`` 이 stale 인 채로 ``npm run dev``
 * 가 살아 있으면 모든 admin UI 패널이 401 로 빈 상태가 되는데, 운영자가 브라우저
 * 새로고침 전까지 알아채지 못했다. 부팅 1회 헬스 체크는 dev 콘솔에서 즉시 인지 가능
 * 한 시그널을 만들어 준다.
 *
 * 설계 결정:
 *
 * - **`nodejs` 런타임에서만 실행**: Edge 런타임에서는 ``.env.local`` 변수 접근이 다르므로
 *   회피. 본 admin 은 단일 운영자용 로컬 도구라 nodejs 런타임이 디폴트.
 * - **실패해도 부팅을 막지 않는다**: 토큰 문제로 ``npm run dev`` 자체를 막으면 운영자가
 *   ``.env.local`` 을 편집할 창구도 같이 잃는다 — 경고만 남기고 서버는 계속 띄운다.
 * - **타임아웃 짧게**: 데몬 미기동 시 ``fetch`` 가 즉시 ECONNREFUSED 로 떨어지지만,
 *   다른 호스트 바인딩으로 들어가면 hang 할 수 있으므로 2초 abort 보호장치를 둔다.
 */

export async function register(): Promise<void> {
  // Edge 런타임에서는 본 훅을 건너뛴다 — instrumentation 은 양쪽 런타임에서 호출될 수 있다.
  if (process.env.NEXT_RUNTIME !== "nodejs") return;

  // ESM 동적 import — 본 파일은 Next 가 빌드 시점에 평가하므로 부수 코드는 register 안으로
  // 가둬둔다.
  const {
    markDaemonUnreachable,
    markTokenChecked,
    markTokenMismatch,
    markTokenMissing,
  } = await import("./src/lib/adminTokenStatus");

  const token = process.env.ADMIN_API_TOKEN;
  if (!token) {
    markTokenMissing();
    return;
  }

  const base = (process.env.ADMIN_API_BASE ?? "http://127.0.0.1:8082").replace(
    /\/$/,
    "",
  );
  const url = `${base}/admin/v1/health`;

  // 2초 abort — 데몬 미기동 또는 잘못된 호스트로 hang 하는 케이스 방어.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 2000);

  try {
    const resp = await fetch(url, {
      method: "GET",
      headers: { Authorization: `Bearer ${token}` },
      signal: controller.signal,
      // Next 캐시는 admin 데이터에 부적합. 항상 실시간.
      cache: "no-store",
    });
    if (resp.status === 401) {
      markTokenMismatch(`GET ${url} → 401`);
      return;
    }
    if (!resp.ok) {
      // 401 이외의 비-200 은 데몬 측 다른 문제 — 토큰 미스매치로 단정짓지 않는다.
      markDaemonUnreachable(`GET ${url} → ${resp.status}`);
      return;
    }
    markTokenChecked(`GET ${url} → 200`);
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    markDaemonUnreachable(`GET ${url} 실패: ${detail}`);
  } finally {
    clearTimeout(timer);
  }
}
