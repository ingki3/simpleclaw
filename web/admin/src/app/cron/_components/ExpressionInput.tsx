"use client";

/**
 * 크론 표현식 입력 + 실시간 검증 + 다음 5회 실행 시각 미리보기.
 *
 * 입력은 controlled. ``invalid`` 상태일 때 ``Input``의 boundary가 danger로
 * swap되고, 한국어 에러 메시지를 헬퍼 라인에 노출한다. 통과한 경우에는
 * ``description``(예: "매일 09:00")과 다음 5회 실행 시각을 보여준다.
 *
 * 검증은 키 입력마다 동기 ``validateCronExpression``으로 수행 — 1년치 분
 * 검색이지만 일반 패턴에서는 수백 ms 이내에 종료한다. 200ms debounce를 두면
 * UX는 동일하면서도 타이핑 중 페인트가 잦지 않다.
 */

import { useEffect, useMemo, useState } from "react";
import { Input } from "@/components/atoms/Input";
import { validateCronExpression, getNextRuns } from "@/lib/cron/expression";

export interface ExpressionInputProps {
  value: string;
  onChange: (next: string) => void;
  /** 표현식 유효성 — 부모(``폼``)가 submit 가드에 사용. */
  onValidityChange?: (valid: boolean) => void;
  /** placeholder. 기본은 자주 쓰는 패턴 안내. */
  placeholder?: string;
}

export function ExpressionInput({
  value,
  onChange,
  onValidityChange,
  placeholder = "예: 0 9 * * * (매일 09:00)",
}: ExpressionInputProps) {
  // 과한 페인트 방지를 위한 debounce. 200ms 정도면 UX 체감과 타협.
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), 200);
    return () => clearTimeout(t);
  }, [value]);

  const validation = useMemo(
    () => validateCronExpression(debounced),
    [debounced],
  );

  // 다음 실행 시각은 통과 시에만 계산. 실패 시 빈 배열.
  const nextRuns = useMemo(() => {
    if (!validation.valid) return [] as Date[];
    try {
      return getNextRuns(debounced, 5);
    } catch {
      return [];
    }
  }, [debounced, validation.valid]);

  // 부모 가드에 검증 결과 통지.
  useEffect(() => {
    onValidityChange?.(validation.valid);
  }, [validation.valid, onValidityChange]);

  const showHelper = debounced.trim().length > 0;

  return (
    <div className="flex flex-col gap-2">
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        invalid={showHelper && !validation.valid}
        aria-describedby="cron-expr-helper"
        className="font-mono"
      />
      <div id="cron-expr-helper" className="min-h-[1.25rem] text-xs">
        {!showHelper ? (
          <span className="text-(--muted-foreground)">
            5필드 cron 문법 (분 시 일 월 요일).
          </span>
        ) : validation.valid ? (
          <span className="text-(--color-success)">{validation.description}</span>
        ) : (
          <span className="text-(--color-error)">{validation.error}</span>
        )}
      </div>
      {nextRuns.length > 0 ? (
        <ul className="flex flex-col gap-1 rounded-(--radius-m) border border-(--border) bg-(--surface) px-3 py-2 text-xs text-(--muted-foreground)">
          <li className="font-medium text-(--foreground)">
            다음 5회 예상 실행
          </li>
          {nextRuns.map((d) => (
            <li key={d.toISOString()} className="font-mono">
              {formatRunTime(d)}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** 사용자 로컬 타임존 기준 ``YYYY-MM-DD HH:mm`` 포맷. 짧고 정렬 가능. */
function formatRunTime(d: Date): string {
  const pad = (n: number) => (n < 10 ? `0${n}` : String(n));
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
