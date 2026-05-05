/**
 * MaskedSecretRow — Molecular. 키 이름 + 마스킹 값 + reveal/copy/rotate (DESIGN.md §3.2, §4.2).
 *
 * SecretField (atomic) 를 wrapping 해 row 레이아웃을 강제 — 키 이름이 시크릿 값보다
 * 먼저 읽혀야 한다는 시각 가이드라인을 박제.
 */

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Code } from "../atoms/Code";
import { SecretField, type SecretFieldProps } from "../atoms/SecretField";

export interface MaskedSecretRowProps
  extends Omit<SecretFieldProps, "className"> {
  /** 키 이름 — `keyring:claude_api_key` 같은 mono 표시. */
  keyName: string;
  /** 우측 추가 메타 (예: 마지막 회전 시각). */
  meta?: ReactNode;
  className?: string;
}

export function MaskedSecretRow({
  keyName,
  meta,
  className,
  ...secretProps
}: MaskedSecretRowProps) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-3 border-b border-(--border) py-2",
        className,
      )}
    >
      <Code className="shrink-0">{keyName}</Code>
      <SecretField {...secretProps} />
      {meta ? (
        <span className="ml-auto text-xs text-(--muted-foreground)">
          {meta}
        </span>
      ) : null}
    </div>
  );
}
