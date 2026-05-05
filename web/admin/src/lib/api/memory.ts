/**
 * Memory API 클라이언트 — Memory 화면(BIZ-49) 전용.
 *
 * Persona와 마찬가지로 본 모듈은 Next.js 측 Route Handler(`/api/memory/*`)를 호출한다.
 * Python `/admin/v1/memory/*`가 들어오면 `fetchAdmin`으로 전환하면 된다.
 */

import type {
  ActiveProjectSummary,
  ActiveProjectsResponse,
  DreamingState,
  GatePolicy,
  MemoryEntry,
  MemoryEntryType,
  MemoryStats,
} from "./memory-server";

export type {
  ActiveProjectSummary,
  ActiveProjectsResponse,
  DreamingState,
  GatePolicy,
  MemoryEntry,
  MemoryEntryType,
  MemoryStats,
};

export interface MemoryIndexResponse {
  stats: MemoryStats;
  file: {
    exists: boolean;
    updatedAt: string | null;
    sizeBytes: number;
    content: string;
  };
  entries: MemoryEntry[];
  dreaming: DreamingState;
}

export interface EntryMutationResponse {
  ok: true;
  undoToken: string;
  entries: MemoryEntry[];
}

async function jsonRequest<T>(
  path: string,
  init: RequestInit & { json?: unknown } = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  let body: BodyInit | undefined;
  if (init.json !== undefined) {
    headers.set("content-type", "application/json");
    body = JSON.stringify(init.json);
  }
  const res = await fetch(path, {
    ...init,
    headers,
    body: init.json !== undefined ? body : init.body,
    cache: "no-store",
  });
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    const detail =
      data && typeof data === "object" && "error" in data
        ? String((data as { error: unknown }).error)
        : `HTTP ${res.status}`;
    const err = new Error(detail);
    (err as { status?: number }).status = res.status;
    (err as { data?: unknown }).data = data;
    throw err;
  }
  return data as T;
}

export function getMemoryIndex(): Promise<MemoryIndexResponse> {
  return jsonRequest<MemoryIndexResponse>("/api/memory/index");
}

export function getActiveProjects(): Promise<ActiveProjectsResponse> {
  return jsonRequest<ActiveProjectsResponse>("/api/memory/active-projects");
}

export function patchMemoryEntry(
  id: string,
  text: string,
): Promise<EntryMutationResponse> {
  return jsonRequest<EntryMutationResponse>("/api/memory/entry", {
    method: "PATCH",
    json: { id, text },
  });
}

export function deleteMemoryEntry(
  id: string,
  confirmation: string,
): Promise<EntryMutationResponse> {
  return jsonRequest<EntryMutationResponse>("/api/memory/entry", {
    method: "DELETE",
    json: { id, confirmation },
  });
}

export function undoMemoryChange(
  token: string,
): Promise<{ ok: true; entries: MemoryEntry[] }> {
  return jsonRequest<{ ok: true; entries: MemoryEntry[] }>("/api/memory/undo", {
    method: "POST",
    json: { token },
  });
}

export function getDreamingStatus(): Promise<DreamingState> {
  return jsonRequest<DreamingState>("/api/memory/dreaming");
}

export function triggerDreaming(): Promise<{
  ok: true;
  state: DreamingState;
}> {
  return jsonRequest<{ ok: true; state: DreamingState }>(
    "/api/memory/dreaming",
    { method: "POST" },
  );
}

/**
 * 다운로드 URL을 만들어 anchor `download`로 클릭하면 파일이 저장된다.
 * 클라이언트가 `<a href={url} download>` 패턴으로 호출한다.
 */
export function exportConversationsUrl(
  from: string | null,
  to: string | null,
): string {
  const params = new URLSearchParams();
  if (from) params.set("from", from);
  if (to) params.set("to", to);
  const qs = params.toString();
  return qs ? `/api/memory/export?${qs}` : "/api/memory/export";
}
