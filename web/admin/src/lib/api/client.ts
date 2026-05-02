/**
 * fetchAdmin — Admin REST API용 단일 fetch 래퍼.
 *
 * 책임:
 *  - **인증 헤더 자동 주입**. 클라이언트(브라우저)는 same-origin 프록시
 *    경로(``/api/admin/...``)로만 요청을 보내고, Next 서버 측 라우트가
 *    ``process.env.ADMIN_API_TOKEN``으로 Bearer 토큰을 추가해 데몬에 위임한다.
 *    서버 측에서 호출되는 경우(예: Storybook의 SSR)에는 토큰을 직접 주입한다.
 *  - **Idempotency-Key 자동 헤더** — POST/PUT/PATCH는 UUID로 멱등키 부여.
 *  - **일관된 에러 처리** — 401/403/4xx/5xx는 ``AdminApiError``로 정규화,
 *    네트워크 실패도 동일 에러로 변환.
 *
 * 디자인 결정:
 *  - 토큰을 브라우저에 노출하지 않기 위해 *Next API 라우트 프록시* 패턴을 채택.
 *    ``src/app/api/admin/[...path]/route.ts``가 같은 origin으로 들어온 요청을
 *    데몬으로 forward하며, 본 함수는 base를 ``/api/admin``로 둔다.
 *  - 영역별 화면이 ``fetchAdmin('/config/llm')``처럼 *데몬 경로 그대로* 호출하면
 *    프록시가 ``/admin/v1`` 접두사를 부여하므로, 콜사이트는 백엔드 docs와 매칭됨.
 */

import {
  AdminApiError,
  classifyStatus,
  type AdminApiErrorDetails,
} from "./errors";
import {
  generateIdempotencyKey,
  shouldAttachIdempotencyKey,
} from "./idempotency";

/** fetchAdmin 호출 옵션 — 표준 ``RequestInit`` 위에 admin 전용 옵션을 얹는다. */
export interface FetchAdminInit extends Omit<RequestInit, "body"> {
  /** JSON 직렬화될 객체. ``body``와 동시에 사용 금지. */
  json?: unknown;
  /** raw body — 일반 RequestInit.body와 동일. */
  body?: BodyInit | null;
  /** 멱등키를 외부에서 명시 — undo retry 등에서 같은 키를 재사용하고 싶을 때. */
  idempotencyKey?: string;
  /** 서버 측 호출이라면 토큰을 직접 주입할 수 있다 (브라우저에선 사용 금지). */
  bearerToken?: string;
  /** 기본 base를 우회할 때 — 테스트/MSW 환경에서 사용. */
  baseUrl?: string;
  /** AbortSignal 별칭 — 인보크 측 가독성. */
  signal?: AbortSignal | null;
}

/**
 * 글로벌 base URL 설정자 — 테스트/스토리북에서 MSW 핸들러로 직접 보낼 때 사용한다.
 * 기본값은 same-origin 프록시 경로 ``/api/admin``.
 */
let _baseUrl = "/api/admin";

export function setAdminApiBaseUrl(url: string): void {
  _baseUrl = url.replace(/\/$/, "");
}

export function getAdminApiBaseUrl(): string {
  return _baseUrl;
}

/** 토스트로 라우팅할 에러를 외부에서 옵트인하기 위한 글로벌 리스너. */
type ErrorListener = (err: AdminApiError) => void;
const _errorListeners = new Set<ErrorListener>();

export function onAdminApiError(listener: ErrorListener): () => void {
  _errorListeners.add(listener);
  return () => {
    _errorListeners.delete(listener);
  };
}

function _emitError(err: AdminApiError): void {
  for (const l of _errorListeners) {
    try {
      l(err);
    } catch {
      // 리스너 실패는 호출 흐름을 막지 않는다.
    }
  }
}

/**
 * fetchAdmin — admin REST 호출의 *유일한* 진입점.
 *
 * @param path  ``/config/llm``처럼 슬래시로 시작하는 경로. base가 자동으로 붙는다.
 * @param init  fetch init + admin 전용 옵션.
 * @returns     2xx면 JSON 파싱 결과(또는 빈 응답이면 ``undefined``).
 * @throws      4xx/5xx/네트워크 실패 시 ``AdminApiError``.
 */
export async function fetchAdmin<T = unknown>(
  path: string,
  init: FetchAdminInit = {},
): Promise<T> {
  const {
    json,
    body,
    idempotencyKey,
    bearerToken,
    baseUrl,
    headers: rawHeaders,
    method = "GET",
    ...rest
  } = init;

  if (json !== undefined && body !== undefined) {
    throw new Error(
      "fetchAdmin: 'json'과 'body'를 동시에 지정할 수 없습니다. 둘 중 하나만 사용하세요.",
    );
  }

  const headers = new Headers(rawHeaders);
  // JSON 페이로드는 자동으로 Content-Type 부여 + 직렬화.
  let finalBody: BodyInit | null | undefined = body;
  if (json !== undefined) {
    headers.set("Content-Type", "application/json");
    finalBody = JSON.stringify(json);
  }
  // POST/PUT/PATCH에는 멱등키 자동 부여(이미 명시된 경우 보존).
  if (shouldAttachIdempotencyKey(method) && !headers.has("Idempotency-Key")) {
    headers.set("Idempotency-Key", idempotencyKey ?? generateIdempotencyKey());
  }
  // 서버 측에서 직접 호출 시에만 Bearer 헤더 부착 — 브라우저에선 프록시가 처리.
  if (bearerToken) {
    headers.set("Authorization", `Bearer ${bearerToken}`);
  }

  const base = (baseUrl ?? _baseUrl).replace(/\/$/, "");
  const url = path.startsWith("http") ? path : `${base}${path}`;

  let response: Response;
  try {
    response = await fetch(url, {
      ...rest,
      method,
      headers,
      body: finalBody,
    });
  } catch (cause) {
    const err = new AdminApiError(
      "데몬에 연결할 수 없어요. 네트워크나 admin 토큰을 확인해 주세요.",
      0,
      "network",
      { body: cause instanceof Error ? cause.message : String(cause) },
    );
    _emitError(err);
    throw err;
  }

  if (!response.ok) {
    const { kind, fallbackMessage } = classifyStatus(response.status);
    const details: AdminApiErrorDetails = {};
    let message = fallbackMessage;
    try {
      const text = await response.text();
      if (text) {
        try {
          const parsed: unknown = JSON.parse(text);
          details.body = parsed;
          if (
            parsed &&
            typeof parsed === "object" &&
            "error" in (parsed as Record<string, unknown>) &&
            typeof (parsed as Record<string, unknown>).error === "string"
          ) {
            message = (parsed as Record<string, string>).error;
          }
          if (
            parsed &&
            typeof parsed === "object" &&
            Array.isArray((parsed as Record<string, unknown>).errors)
          ) {
            details.errors = (
              parsed as { errors: unknown[] }
            ).errors.filter((e): e is string => typeof e === "string");
          }
        } catch {
          details.body = text;
          message = text.slice(0, 200);
        }
      }
    } catch {
      // 본문 read 실패는 무시 — fallbackMessage 사용.
    }
    const err = new AdminApiError(message, response.status, kind, details);
    _emitError(err);
    throw err;
  }

  // 2xx — 본문이 있으면 JSON으로 파싱.
  const text = await response.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    // JSON이 아닌 2xx 응답은 텍스트 그대로 반환.
    return text as unknown as T;
  }
}
