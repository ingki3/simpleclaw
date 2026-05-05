/**
 * Code (Mono) — Atomic. 인라인 코드/시크릿 참조 (DESIGN.md §3.1).
 *
 * 사용처: 키 이름, `keyring:claude_api_key` 같은 시크릿 참조, 짧은 명령어.
 * block 옵션: 여러 줄 코드 블록.
 */

import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface CodeProps extends HTMLAttributes<HTMLElement> {
  block?: boolean;
  children: ReactNode;
}

export function Code({ block, className, children, ...rest }: CodeProps) {
  if (block) {
    return (
      <pre
        className={cn(
          "rounded-(--radius-m) bg-(--surface) p-3 text-xs font-mono text-(--foreground) whitespace-pre-wrap",
          className,
        )}
        {...rest}
      >
        <code>{children}</code>
      </pre>
    );
  }
  return (
    <code
      className={cn(
        "rounded-(--radius-sm) bg-(--surface) px-1.5 py-0.5 text-xs font-mono text-(--foreground)",
        className,
      )}
      {...rest}
    >
      {children}
    </code>
  );
}
