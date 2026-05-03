"use client";

/**
 * LogLevelControl — 런타임 로그 레벨 조회/변경 패널.
 *
 * BIZ-37 후속 작업으로 ``logging.level`` 정책 키 신설이 합의되기 전까지는 백엔드의
 * ``GET /admin/v1/config/logging``이 404를 돌려준다. 본 컴포넌트는 그 케이스를
 * "준비 중" 상태로 표시해 운영자가 화면 자체의 결함이라 오해하지 않도록 한다.
 *
 * 백엔드가 합류하면 그 자리에 ``useAdminMutation('/admin/v1/config/logging')``으로
 * PATCH가 자동 활성화되도록 설계 — 코드 변경 없이 토큰 조정만으로 활성화된다.
 */

import { useEffect, useState } from "react";
import { Button } from "@/components/atoms/Button";
import { Badge } from "@/components/atoms/Badge";
import { useAdminQuery, useAdminMutation } from "@/lib/api/hooks";
import { AdminApiError } from "@/lib/api/errors";
import { LEVEL_TOKENS, type LogLevelToken } from "@/lib/api/logs";

interface LoggingConfig {
  level?: string;
}

const LEVEL_LABEL: Record<LogLevelToken, string> = {
  debug: "debug",
  info: "info",
  warn: "warn",
  error: "error",
};

const TOKEN_TO_API = {
  debug: "DEBUG",
  info: "INFO",
  warn: "WARNING",
  error: "ERROR",
} as const;

const API_TO_TOKEN: Record<string, LogLevelToken> = {
  DEBUG: "debug",
  INFO: "info",
  WARNING: "warn",
  ERROR: "error",
  WARN: "warn",
};

export function LogLevelControl() {
  const query = useAdminQuery<LoggingConfig>("/admin/v1/config/logging", {
    // 404는 BIZ-37 후속 작업 대기 신호 — 자동 retry 끄기.
    shouldRetryOnError: false,
  });
  const mutation = useAdminMutation<LoggingConfig>("/admin/v1/config/logging");

  const [pending, setPending] = useState<LogLevelToken | undefined>(undefined);

  // 서버 응답이 도착하면 pending 초기화.
  useEffect(() => {
    if (!query.data) return;
    const token = query.data.level
      ? API_TO_TOKEN[query.data.level.toUpperCase()] ?? undefined
      : undefined;
    setPending(token);
  }, [query.data]);

  // BIZ-37 미합류 신호 — 404. 그 외 에러는 별도 표기.
  const notReady =
    query.error instanceof AdminApiError && query.error.kind === "not_found";

  const apply = async () => {
    if (!pending) return;
    try {
      await mutation.trigger({
        method: "PATCH",
        json: { level: TOKEN_TO_API[pending] },
        invalidate: ["/admin/v1/config/logging"],
      });
    } catch {
      // 토스트는 페이지 외곽의 ToastProvider에서 별도로 띄울 수 있다 — 본 컴포넌트는
      // mutation.error를 직접 출력해 사용자에게 즉시 노출한다.
    }
  };

  const dirty =
    pending !== undefined &&
    query.data?.level !== undefined &&
    pending !== API_TO_TOKEN[query.data.level.toUpperCase()];

  return (
    <section
      className="flex flex-col gap-3 rounded-(--radius-m) border border-(--border) bg-(--card) p-4"
      aria-labelledby="log-level-heading"
    >
      <div className="flex items-center justify-between gap-2">
        <h2
          id="log-level-heading"
          className="text-sm font-semibold text-(--foreground-strong)"
        >
          로그 레벨
        </h2>
        {notReady ? (
          <Badge tone="neutral">준비 중 (BIZ-37 후속)</Badge>
        ) : query.data?.level ? (
          <Badge tone="info">현재 {query.data.level}</Badge>
        ) : null}
      </div>

      {notReady ? (
        <p className="text-xs text-(--muted-foreground)">
          백엔드가 <code className="font-mono">logging.level</code> 정책 키를 노출하면
          이 패널에서 즉시 변경할 수 있어요. 그때까지는 <code className="font-mono">
          config.yaml</code>의 <code>logging.level</code>을 직접 수정해 주세요.
        </p>
      ) : query.error ? (
        <p className="text-xs text-(--color-error)">
          현재 레벨을 불러오지 못했습니다 — {query.error.message}
        </p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-1" role="radiogroup" aria-label="로그 레벨 선택">
            {LEVEL_TOKENS.map((tk) => (
              <button
                key={tk}
                type="button"
                role="radio"
                aria-checked={pending === tk}
                onClick={() => setPending(tk)}
                className={
                  "rounded-(--radius-sm) border px-2 py-1 text-xs " +
                  (pending === tk
                    ? "border-(--primary) bg-(--primary-tint) text-(--primary)"
                    : "border-(--border) text-(--muted-foreground) hover:bg-(--surface)")
                }
              >
                {LEVEL_LABEL[tk]}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="primary"
              disabled={!dirty || mutation.isMutating}
              onClick={apply}
            >
              {mutation.isMutating ? "적용 중…" : "적용"}
            </Button>
            <span className="text-xs text-(--muted-foreground)">
              ↻ 재시작 없이 즉시 반영
            </span>
            {mutation.error ? (
              <span className="text-xs text-(--color-error)">
                적용 실패: {mutation.error.message}
              </span>
            ) : null}
          </div>
        </>
      )}
    </section>
  );
}
