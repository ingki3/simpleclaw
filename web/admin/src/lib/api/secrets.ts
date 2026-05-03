/**
 * Secrets API 래퍼 — ``/admin/v1/secrets/*``를 타입화해 노출한다.
 *
 * BIZ-50 Secrets 화면(admin.pen Screen 07 / docs/admin-requirements.md §1, §2.2,
 * §4.1)을 구현하기 위한 클라이언트 어댑터. BIZ-43에서 도입된 ``client.ts`` 위에
 * 얇게 얹어 다음 4개 작업을 한 곳에서 다룬다.
 *
 *  - ``listSecrets``        — 키 메타데이터 (이름·백엔드·마지막 회전 시각). 값은 절대 안 옴.
 *  - ``revealSecret``       — 일회성 평문 + 15초 TTL nonce. UI는 *마지막 4자리만* 5초간 노출.
 *  - ``rotateSecret``       — 새 값을 저장. 동일 엔드포인트가 신규 추가에도 쓰인다.
 *  - ``findSecretReferences`` — config.yaml dict에서 ``backend:name`` 참조를 역색인.
 *
 * 설계 결정:
 *  - 백엔드의 ``rotate`` 핸들러는 dry-run 모드를 별도로 두지 않는다. 대신 *클라이언트
 *    측 영향 분석*으로 충분하다 — config.yaml은 마스킹된 참조 문자열만 담으므로
 *    트리를 한 번 훑어 일치하는 경로를 모으면 “rotate 시 동시에 갱신될 컴포넌트”를
 *    안전하게 보여줄 수 있다(평문 노출 위험 없음).
 *  - rotate에 사용한 idempotency-key는 client.ts가 자동 부여 — 같은 회전이 두 번 적용
 *    되지 않도록 데몬이 1회만 처리한다.
 */

import { fetchAdmin } from "./client";

/** 백엔드 라벨 — admin_api._BACKEND_LABELS와 정합. */
export type SecretBackend = "env" | "keyring" | "file";

export const SECRET_BACKENDS: readonly SecretBackend[] = [
  "env",
  "keyring",
  "file",
] as const;

/** 단일 시크릿 메타. 값은 절대 포함되지 않는다. */
export interface SecretMeta {
  name: string;
  backend: SecretBackend | string;
  /** ISO 8601 — 백엔드는 마지막 ``secret.rotate`` audit ts를 그대로 반환. */
  last_rotated_at: string | null;
}

export interface RevealSecretResponse {
  name: string;
  backend: string;
  /** 평문 — UI는 마지막 4자리만 5초간 노출 후 즉시 폐기. */
  value: string;
  /** 일회성 nonce — 백엔드가 15초 후 자동 무효화. UI는 메모리에서도 즉시 비운다. */
  nonce: string;
  expires_in_seconds: number;
}

export interface RotateSecretResponse {
  outcome: "applied" | string;
  audit_id: string;
  backend: string;
  name: string;
}

/** 회전이 영향 줄 컴포넌트 — config.yaml 트리에서 dotted-path로 수집한 결과. */
export interface SecretReference {
  /** ``llm.providers.claude.api_key`` 같은 점 표기 경로. */
  path: string;
  /** UI 배지로 묶을 영역(``llm`` / ``webhook`` / ``telegram`` / ``mcp`` 등). */
  area: string;
  /** ``keyring`` / ``env`` / ``file`` — 참조 문법에서 추출한 백엔드. */
  backend: string;
}

// ---------------------------------------------------------------------------
// REST 호출
// ---------------------------------------------------------------------------

/** 등록된 시크릿 메타데이터 — 정렬은 (backend, name) 사전순. */
export async function listSecrets(): Promise<SecretMeta[]> {
  const data = await fetchAdmin<{ secrets: SecretMeta[] }>("/secrets");
  const items = data.secrets ?? [];
  return [...items].sort((a, b) => {
    const byBackend = String(a.backend).localeCompare(String(b.backend));
    if (byBackend !== 0) return byBackend;
    return a.name.localeCompare(b.name);
  });
}

/**
 * 시크릿 평문을 일회성으로 받아온다 — 호출자는 즉시 ``slice(-4)`` 외에는 보여주지 말 것.
 *
 * 본 함수의 반환값을 그대로 DOM에 그리면 5초 후 자동 마스킹이 의미를 잃으므로,
 * 호출 측은 *마지막 4자리만* 추출한 뒤 메모리에서 ``value``를 즉시 비워야 한다.
 */
export function revealSecret(
  name: string,
  backend?: string,
): Promise<RevealSecretResponse> {
  const query = backend ? `?backend=${encodeURIComponent(backend)}` : "";
  return fetchAdmin<RevealSecretResponse>(
    `/secrets/${encodeURIComponent(name)}/reveal${query}`,
    { method: "POST", json: {} },
  );
}

/** 시크릿을 새 값으로 회전 — 동일 엔드포인트가 신규 추가에도 사용된다. */
export function rotateSecret(
  name: string,
  value: string,
  backend?: SecretBackend,
): Promise<RotateSecretResponse> {
  return fetchAdmin<RotateSecretResponse>(
    `/secrets/${encodeURIComponent(name)}/rotate`,
    { method: "POST", json: { value, backend } },
  );
}

// ---------------------------------------------------------------------------
// 영향 분석 — config.yaml 트리 역색인
// ---------------------------------------------------------------------------

const REF_PATTERN = /^(env|keyring|file):(.+)$/;

/**
 * ``GET /admin/v1/config``에서 받은 머지 dict에서 시크릿 참조를 역색인한다.
 *
 * 다음 두 형태를 모두 잡는다.
 *
 *   1) "keyring:claude_api_key" — 시크릿 참조 문법(우선).
 *   2) 시크릿성 키 이름(``*_token``, ``*_key``, ``*_secret``, ``*password``)인데 값이
 *      마스킹되어 있는 경우(``••••1234``) — 참조가 아닌 평문이 vault에 저장돼 있고 키
 *      이름이 일치할 때의 폴백. (백엔드는 응답을 마스킹하지만 키 이름은 보존한다.)
 *
 * @param config        ``GET /admin/v1/config``의 ``config`` 필드.
 * @param secretName    찾고자 하는 시크릿 이름.
 * @returns             일치한 참조 — UI는 상위 영역별로 묶어 배지를 그린다.
 */
export function findSecretReferences(
  config: unknown,
  secretName: string,
): SecretReference[] {
  const refs: SecretReference[] = [];
  walk(config, [], (value, path) => {
    if (typeof value !== "string") return;
    const m = REF_PATTERN.exec(value);
    if (m && m[2] === secretName) {
      refs.push({
        path: path.join("."),
        area: path[0] ?? "(root)",
        backend: m[1],
      });
    }
  });
  return refs;
}

function walk(
  value: unknown,
  path: string[],
  visit: (value: unknown, path: string[]) => void,
): void {
  visit(value, path);
  if (Array.isArray(value)) {
    value.forEach((item, idx) => walk(item, [...path, String(idx)], visit));
    return;
  }
  if (value && typeof value === "object") {
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      walk(v, [...path, k], visit);
    }
  }
}

/** 점 표기 경로에서 사람이 읽는 영역 라벨로 변환. */
export function areaLabel(area: string): string {
  switch (area) {
    case "llm":
      return "LLM";
    case "webhook":
      return "Webhook";
    case "telegram":
      return "Telegram";
    case "mcp":
      return "MCP";
    case "voice":
      return "Voice";
    case "channels":
      return "Channels";
    default:
      return area;
  }
}

/**
 * 머지된 config 트리를 가져온다 — 영향 분석 전용. 시크릿은 백엔드가 마스킹한
 * 형태로만 들어와 평문이 클라이언트 메모리에 머무를 위험이 없다.
 */
export async function fetchConfigTree(): Promise<unknown> {
  const data = await fetchAdmin<{ config: unknown }>("/config");
  return data.config ?? {};
}
