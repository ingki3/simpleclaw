"use client";

/**
 * useAdminResource — `fetchAdmin`을 폴링 가능한 React 훅으로 감싼다.
 *
 * BIZ-43의 `useAdminQuery`/`useAdminMutation`이 정착되기 전, BIZ-44 대시보드의
 * 5초 폴링·로딩/에러/빈 상태를 표준 훅 한 곳에서 처리하기 위한 최소 구현.
 * 외부 데이터 라이브러리 의존을 추가하지 않고, 마운트/언마운트 안전성과 폴링 정지만 보장한다.
 *
 * 사용 측 계약:
 * - `data`/`error`/`isLoading`/`refetch`를 반환.
 * - `intervalMs`가 양수면 마운트된 동안 주기 폴링. 탭이 백그라운드일 때는 멈춘다(visibility).
 * - `enabled === false`면 fetch를 보내지 않는다 (의존이 채워지길 기다리는 케이스).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { AdminApiError, fetchAdmin } from "./fetch-admin";

export interface UseAdminResourceOptions {
  /** 폴링 주기(ms). 0이거나 미지정이면 단발 fetch. */
  intervalMs?: number;
  /** false이면 fetch를 시도하지 않는다. */
  enabled?: boolean;
}

export interface UseAdminResourceState<T> {
  data: T | undefined;
  error: AdminApiError | undefined;
  isLoading: boolean;
  /** 첫 로드 이후 갱신 중인지(폴링 진행 표시용). */
  isRefreshing: boolean;
  refetch: () => void;
}

export function useAdminResource<T>(
  path: string,
  { intervalMs = 0, enabled = true }: UseAdminResourceOptions = {},
): UseAdminResourceState<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<AdminApiError | undefined>(undefined);
  const [isLoading, setIsLoading] = useState<boolean>(enabled);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);

  const mountedRef = useRef(true);
  const hasLoadedRef = useRef(false);

  const run = useCallback(async () => {
    if (!enabled) return;
    if (hasLoadedRef.current) {
      setIsRefreshing(true);
    } else {
      setIsLoading(true);
    }
    try {
      const next = await fetchAdmin<T>(path);
      if (!mountedRef.current) return;
      setData(next);
      setError(undefined);
      hasLoadedRef.current = true;
    } catch (err) {
      if (!mountedRef.current) return;
      if (err instanceof AdminApiError) {
        setError(err);
      } else {
        setError(
          new AdminApiError({
            status: 0,
            message: err instanceof Error ? err.message : "Unknown error",
          }),
        );
      }
    } finally {
      if (mountedRef.current) {
        setIsLoading(false);
        setIsRefreshing(false);
      }
    }
  }, [path, enabled]);

  // 마운트 / path 변경 시 즉시 1회 fetch.
  useEffect(() => {
    mountedRef.current = true;
    hasLoadedRef.current = false;
    if (enabled) {
      void run();
    } else {
      setIsLoading(false);
    }
    return () => {
      mountedRef.current = false;
    };
  }, [run, enabled]);

  // 폴링 — 백그라운드 탭에서는 정지(불필요한 트래픽 방지).
  useEffect(() => {
    if (!enabled || !intervalMs || intervalMs <= 0) return;
    let timer: ReturnType<typeof setInterval> | null = null;

    const start = () => {
      if (timer !== null) return;
      timer = setInterval(() => {
        if (document.visibilityState === "visible") {
          void run();
        }
      }, intervalMs);
    };
    const stop = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        // 탭 복귀 즉시 한 번 동기화.
        void run();
        start();
      } else {
        stop();
      }
    };

    start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [intervalMs, enabled, run]);

  return { data, error, isLoading, isRefreshing, refetch: run };
}
