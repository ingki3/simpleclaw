/**
 * fetchAdmin — Admin Backend(`/admin/v1/*`) 호출 헬퍼.
 *
 * BIZ-43에서 SWR/TanStack 기반의 풀 클라이언트(`useAdminQuery`/`useUndo` 등)가
 * 정착되기 전, 대시보드(BIZ-44)와 같은 초기 화면이 백엔드를 호출하기 위한 최소 어댑터다.
 * BIZ-43이 들어오면 본 모듈은 본격 클라이언트의 저수준 fetch 어댑터로 흡수된다.
 *
 * 설계 결정:
 * - 베이스 URL은 `NEXT_PUBLIC_ADMIN_API_BASE` (기본 `http://127.0.0.1:8765`).
 * - Bearer 토큰은 `NEXT_PUBLIC_ADMIN_API_TOKEN` (Admin은 단일 운영자 로컬 도구 — DESIGN.md §1).
 * - 401/403/5xx는 `AdminApiError`로 통일해 호출부가 동일한 분기로 처리할 수 있게 한다.
 * - POST/PUT/PATCH는 `Idempotency-Key`를 자동 부여 (백엔드의 idempotent 처리 보장).
 */

export interface AdminApiErrorPayload {
  status: number;
  message: string;
  body?: unknown;
}

export class AdminApiError extends Error {
  readonly status: number;
  readonly body?: unknown;

  constructor({ status, message, body }: AdminApiErrorPayload) {
    super(message);
    this.name = "AdminApiError";
    this.status = status;
    this.body = body;
  }
}

const DEFAULT_BASE = "http://127.0.0.1:8765";

function readBase(): string {
  const raw = process.env.NEXT_PUBLIC_ADMIN_API_BASE?.trim();
  return raw && raw.length > 0 ? raw.replace(/\/$/, "") : DEFAULT_BASE;
}

function readToken(): string | null {
  const raw = process.env.NEXT_PUBLIC_ADMIN_API_TOKEN?.trim();
  return raw && raw.length > 0 ? raw : null;
}

function newIdempotencyKey(): string {
  // 브라우저 crypto 우선, 폴백은 시각+랜덤. 데몬은 동일 키 재요청 시 동일 응답을 보장해야 하므로
  // 충돌 가능성이 매우 낮은 형식이면 충분하다.
  const c =
    typeof globalThis !== "undefined"
      ? (globalThis.crypto as Crypto | undefined)
      : undefined;
  if (c?.randomUUID) return c.randomUUID();
  return `idem-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** Admin Backend로 JSON 요청을 보내고 파싱된 본문을 반환한다. */
export async function fetchAdmin<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const base = readBase();
  const token = readToken();
  const url = `${base}${path.startsWith("/") ? path : `/${path}`}`;

  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const method = (init.method ?? "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD" && !headers.has("Idempotency-Key")) {
    headers.set("Idempotency-Key", newIdempotencyKey());
  }

  let res: Response;
  try {
    res = await fetch(url, { ...init, headers });
  } catch (err) {
    // 네트워크 에러는 status 0으로 정규화 — 화면에서 "연결 실패" 빈 상태 분기에 사용.
    throw new AdminApiError({
      status: 0,
      message: err instanceof Error ? err.message : "Network error",
    });
  }

  if (!res.ok) {
    let parsed: unknown = undefined;
    try {
      parsed = await res.json();
    } catch {
      // 본문이 JSON이 아닐 수 있음 — 무시하고 status만 노출.
    }
    const message =
      (parsed && typeof parsed === "object" && "error" in parsed
        ? String((parsed as { error: unknown }).error)
        : null) ?? `HTTP ${res.status}`;
    throw new AdminApiError({ status: res.status, message, body: parsed });
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
