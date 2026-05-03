/**
 * Idempotency-Key 생성 — POST/PUT 요청에 자동 첨부.
 *
 * 같은 사용자 액션을 네트워크 중복 시도로부터 보호하기 위해 클라이언트 측에서
 * UUIDv4 형태의 키를 부여한다. 서버는 키를 단순 audit/log 보조 정보로만 쓰며
 * 멱등성 보장은 핸들러별로 위임된다(예: secret rotate는 재호출 위험이 큼).
 *
 * Web Crypto의 ``randomUUID``를 우선 사용하고, polyfill이 없는 환경(테스트 등)에선
 * 보조 RNG로 폴백한다.
 */

export function generateIdempotencyKey(): string {
  // 모던 브라우저 / Node 19+ 가지는 표준 API.
  if (
    typeof globalThis !== "undefined" &&
    typeof globalThis.crypto !== "undefined" &&
    typeof globalThis.crypto.randomUUID === "function"
  ) {
    return globalThis.crypto.randomUUID();
  }
  // 폴백 — 테스트/SSR에서 crypto가 잘려 있을 때만 닿는다.
  const rnd = () => Math.random().toString(16).slice(2, 10).padStart(8, "0");
  return `${rnd()}-${rnd().slice(0, 4)}-4${rnd().slice(0, 3)}-a${rnd().slice(0, 3)}-${rnd()}${rnd().slice(0, 4)}`;
}

/** 메서드가 멱등키를 동반해야 하는지 — POST/PUT/PATCH는 모두 동반한다. */
export function shouldAttachIdempotencyKey(method: string | undefined): boolean {
  if (!method) return false;
  const m = method.toUpperCase();
  return m === "POST" || m === "PUT" || m === "PATCH";
}
