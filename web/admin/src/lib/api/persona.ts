/**
 * Persona API 클라이언트 — Admin UI용 fetch 헬퍼.
 *
 * BIZ-43(공통 API 클라이언트)이 아직 머지되지 않았으므로, 본 모듈은 BIZ-46
 * 한정의 얇은 fetch 래퍼만 제공한다. BIZ-43이 들어오면 `fetchAdmin`을 사용하도록
 * 교체할 수 있게 시그니처를 단순하게 유지한다.
 *
 * 백엔드 `/admin/v1/persona/*` Python FastAPI 엔드포인트는 후속 이슈에서 신설된다.
 * 현 단계에서는 같은 리포의 Next.js Route Handler(`/api/persona/*`)가 로컬
 * `.agent/*.md` 파일을 읽고 쓰는 어댑터로 동작한다.
 */

export type PersonaFileType = "soul" | "agent" | "user" | "memory";

export interface PersonaFileMeta {
  /** AGENT/USER/MEMORY/SOUL 중 하나. */
  type: PersonaFileType;
  /** 파일명 (예: "AGENT.md"). */
  filename: string;
  /** 파일이 디스크에 존재하는지 여부. 없으면 빈 새 파일로 취급. */
  exists: boolean;
  /** 본문 텍스트(읽지 않은 경우 빈 문자열). */
  content: string;
  /** 토큰 수 (cl100k_base 근사). */
  tokens: number;
  /** 토큰 예산(전 파일 합산 기준). */
  tokenBudget: number;
  /** ISO8601 — 마지막 수정 시각. */
  updatedAt: string | null;
}

export interface PersonaListResponse {
  files: PersonaFileMeta[];
  /** 전 파일 합산 토큰 수. */
  totalTokens: number;
  /** 합산 토큰 예산. */
  tokenBudget: number;
}

export interface PersonaPutResult {
  ok: true;
  type: PersonaFileType;
  tokens: number;
  totalTokens: number;
  /** dry-run인 경우 true. */
  dryRun: boolean;
  /** undo 토큰 — 5분 윈도. */
  undoToken?: string;
  /** 적용 모드 — hot-reload 가능한지(persona는 항상 가능). */
  hotReloadable: boolean;
}

export interface PersonaResolveResponse {
  /** 최종 어셈블된 시스템 프롬프트. */
  assembledText: string;
  /** 어셈블 후 토큰 수. */
  tokenCount: number;
  /** 토큰 예산. */
  tokenBudget: number;
  /** 절삭 발생 여부. */
  wasTruncated: boolean;
}

export interface PersonaUndoResult {
  ok: true;
  type: PersonaFileType;
  /** 복원된 파일 본문. */
  content: string;
  tokens: number;
}

const BASE = "/api/persona";

export async function listPersona(): Promise<PersonaListResponse> {
  const res = await fetch(BASE, { cache: "no-store" });
  if (!res.ok) throw new Error(`listPersona failed: ${res.status}`);
  return res.json();
}

export async function getPersona(
  type: PersonaFileType,
): Promise<PersonaFileMeta> {
  const res = await fetch(`${BASE}/${type}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`getPersona ${type} failed: ${res.status}`);
  return res.json();
}

export async function putPersona(
  type: PersonaFileType,
  content: string,
  options?: { dryRun?: boolean; idempotencyKey?: string },
): Promise<PersonaPutResult> {
  const params = options?.dryRun ? "?dry_run=true" : "";
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (options?.idempotencyKey) {
    headers["Idempotency-Key"] = options.idempotencyKey;
  }
  const res = await fetch(`${BASE}/${type}${params}`, {
    method: "PUT",
    headers,
    body: JSON.stringify({ content }),
  });
  if (!res.ok) {
    const msg = await res.text().catch(() => "");
    throw new Error(`putPersona ${type} failed: ${res.status} ${msg}`);
  }
  return res.json();
}

export async function deletePersona(
  type: PersonaFileType,
): Promise<{ ok: true; type: PersonaFileType }> {
  const res = await fetch(`${BASE}/${type}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`deletePersona ${type} failed: ${res.status}`);
  return res.json();
}

export async function resolvePersona(): Promise<PersonaResolveResponse> {
  const res = await fetch(`${BASE}/resolve`, { cache: "no-store" });
  if (!res.ok) throw new Error(`resolvePersona failed: ${res.status}`);
  return res.json();
}

export async function undoPersona(token: string): Promise<PersonaUndoResult> {
  const res = await fetch(`${BASE}/undo`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  if (!res.ok) throw new Error(`undoPersona failed: ${res.status}`);
  return res.json();
}
