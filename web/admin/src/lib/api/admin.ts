/**
 * Admin API 클라이언트 — 데몬의 ``/admin/v1/*`` 엔드포인트(BIZ-41)와 통신한다.
 *
 * 본 모듈은 BIZ-43에서 도입될 공식 ``fetchAdmin`` 헬퍼/훅의 자리를 미리 채우는
 * 최소 어댑터다. 응답 envelope(``{ok, data, error}``), Bearer 인증, JSON 파싱,
 * dry-run 쿼리, 표준 에러 메시지를 한 곳에서 처리해 영역별 페이지가 fetch 호출
 * 상세를 몰라도 되도록 한다. BIZ-43이 머지되면 본 파일은 그쪽으로 흡수된다.
 *
 * 토큰 주입 정책:
 *  - 우선순위 1: ``NEXT_PUBLIC_ADMIN_API_TOKEN`` (개발 편의용 — 운영에서 비권장)
 *  - 우선순위 2: 같은 오리진의 reverse-proxy가 ``Authorization`` 헤더를 주입
 *  - 그 외에는 토큰 없이 호출 → 데몬이 401 응답하면 호출자에게 노출.
 */

const DEFAULT_BASE_URL =
  process.env.NEXT_PUBLIC_ADMIN_API_URL || "http://127.0.0.1:8082";

const DEFAULT_TOKEN =
  process.env.NEXT_PUBLIC_ADMIN_API_TOKEN || "";

/**
 * 데몬 응답 — admin_api._json_ok는 payload dict를 그대로 반환하고,
 * _json_error는 ``{error: string, ...details}`` 형태로 반환한다.
 */
export class AdminAPIError extends Error {
  status: number;
  details?: unknown;

  constructor(status: number, message: string, details?: unknown) {
    super(message);
    this.name = "AdminAPIError";
    this.status = status;
    this.details = details;
  }
}

export interface FetchAdminOptions extends Omit<RequestInit, "body"> {
  /** JSON 본문 — 자동으로 ``JSON.stringify`` 후 ``Content-Type`` 헤더가 부여된다. */
  json?: unknown;
  /** ``?dry_run=true``를 자동으로 붙인다. */
  dryRun?: boolean;
  /** 추가 쿼리 파라미터. */
  query?: Record<string, string | number | boolean | undefined>;
}

/**
 * Admin API 호출의 단일 진입점.
 *
 * - JSON in / JSON out을 가정해 envelope을 풀고 데이터만 반환한다.
 * - 비 2xx거나 envelope이 ``ok=false``면 ``AdminAPIError``를 던진다.
 * - POST/PUT/PATCH는 호출자가 직접 ``Idempotency-Key``를 지정하지 않은 경우
 *   ``crypto.randomUUID()``로 자동 부여 — 동일 요청 중복 적용 방지.
 */
export async function fetchAdmin<T>(
  path: string,
  options: FetchAdminOptions = {},
): Promise<T> {
  const { json, dryRun, query, headers, method, ...rest } = options;
  const url = new URL(path, DEFAULT_BASE_URL);
  if (dryRun) url.searchParams.set("dry_run", "true");
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v === undefined) continue;
      url.searchParams.set(k, String(v));
    }
  }

  const finalHeaders = new Headers(headers);
  if (DEFAULT_TOKEN && !finalHeaders.has("Authorization")) {
    finalHeaders.set("Authorization", `Bearer ${DEFAULT_TOKEN}`);
  }
  let body: BodyInit | undefined;
  if (json !== undefined) {
    finalHeaders.set("Content-Type", "application/json");
    body = JSON.stringify(json);
  }
  const verb = (method ?? (json !== undefined ? "POST" : "GET")).toUpperCase();
  if (
    (verb === "POST" || verb === "PUT" || verb === "PATCH") &&
    !finalHeaders.has("Idempotency-Key")
  ) {
    // crypto.randomUUID는 모던 브라우저/Node 19+에서 가용. SSR 환경에서도 동작.
    const id =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    finalHeaders.set("Idempotency-Key", id);
  }

  const res = await fetch(url.toString(), {
    method: verb,
    headers: finalHeaders,
    body,
    ...rest,
  });

  // 본문이 비어있는 응답(예: 204)에 대비 — text 길이 0이면 빈 객체로 처리.
  const text = await res.text();
  let payload: Record<string, unknown> | null = null;
  if (text) {
    try {
      payload = JSON.parse(text) as Record<string, unknown>;
    } catch {
      throw new AdminAPIError(
        res.status,
        `응답 본문 JSON 파싱 실패: ${text.slice(0, 200)}`,
      );
    }
  }

  if (!res.ok) {
    const message =
      payload && typeof payload.error === "string"
        ? payload.error
        : `HTTP ${res.status}`;
    throw new AdminAPIError(res.status, message, payload ?? undefined);
  }
  if (!payload) {
    // 2xx + 빈 본문 — 호출자가 ``T = void``로 처리하길 기대한다.
    return undefined as unknown as T;
  }
  return payload as T;
}
