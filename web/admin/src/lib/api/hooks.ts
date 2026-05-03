"use client";

/**
 * useAdminQuery / useAdminMutation — Admin REST 호출의 React 훅 표준.
 *
 * SWR을 채택했다(DESIGN.md 부록 C 참고).
 *  - 단일 운영자/단일 워크스페이스이므로 글로벌 캐시·낙관적 업데이트보다 *불러오고
 *    재검증*이 더 잘 어울린다.
 *  - 번들 풋프린트(약 4kB gzip)가 작아 dashboard에서도 가볍다.
 *  - PATCH/POST 같은 mutation은 ``useSWRMutation``으로 별도 키 분리.
 *
 * 본 훅은 다음을 표준화한다:
 *  - 키는 백엔드 path 자체("/config/llm"). SWR이 그대로 캐시 키로 사용한다.
 *  - 에러는 ``AdminApiError`` 단일 타입. 화면 코드는 ``error.kind``로 분기.
 *  - mutation 성공 시 토스트/Undo 등록은 별도 토스트 브릿지(``./undo``)에서 옵트인.
 */

import useSWR, {
  type SWRConfiguration,
  type SWRResponse,
  type Key,
  useSWRConfig,
} from "swr";
import useSWRMutation, {
  type SWRMutationConfiguration,
  type SWRMutationResponse,
} from "swr/mutation";
import { fetchAdmin, type FetchAdminInit } from "./client";
import { AdminApiError } from "./errors";

/** SWR fetcher — admin API 클라이언트로 위임. */
const adminFetcher = <T>(path: string): Promise<T> =>
  fetchAdmin<T>(path, { method: "GET" });

/**
 * 읽기 전용 GET 호출에 사용하는 훅.
 *
 * @param path  ``null``이면 호출을 건너뛴다(조건부 fetch).
 */
export function useAdminQuery<T>(
  path: Key,
  config?: SWRConfiguration<T, AdminApiError>,
): SWRResponse<T, AdminApiError> {
  return useSWR<T, AdminApiError>(path, adminFetcher, config);
}

export interface AdminMutationArg {
  /** HTTP 메서드 — 기본 PATCH. */
  method?: "POST" | "PUT" | "PATCH" | "DELETE";
  /** JSON 직렬화될 body. */
  json?: unknown;
  /** 추가 fetch 옵션. */
  init?: FetchAdminInit;
  /** 동일 키로 invalidate할 path 목록 — 주로 mutation 후 화면 reload. */
  invalidate?: string[];
}

/**
 * 변경(POST/PUT/PATCH/DELETE)에 사용하는 훅.
 *
 * - SWR의 useSWRMutation 위에 얇은 어댑터.
 * - 인자로 ``{ method, json }``을 받아 백엔드를 호출하고, 성공 시 ``invalidate``에
 *   적힌 키를 broadcast해 useAdminQuery 캐시를 갱신한다.
 *
 * @example
 *   const { trigger, isMutating } = useAdminMutation('/config/llm');
 *   await trigger({ method: 'PATCH', json: patch, invalidate: ['/config/llm'] });
 */
export function useAdminMutation<TResult = unknown>(
  path: string,
  config?: SWRMutationConfiguration<TResult, AdminApiError, string, AdminMutationArg>,
): SWRMutationResponse<TResult, AdminApiError, string, AdminMutationArg> {
  const { mutate } = useSWRConfig();
  return useSWRMutation<TResult, AdminApiError, string, AdminMutationArg>(
    path,
    async (key: string, { arg }: { arg: AdminMutationArg }) => {
      const result = await fetchAdmin<TResult>(key, {
        ...(arg.init ?? {}),
        method: arg.method ?? "PATCH",
        json: arg.json,
      });
      // 성공 후 invalidate 키들에 broadcast — 화면 자동 reload.
      if (arg.invalidate?.length) {
        await Promise.all(arg.invalidate.map((k) => mutate(k)));
      }
      return result;
    },
    config,
  );
}
