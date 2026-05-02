"use client";

/**
 * SecretField — DESIGN.md §4.2 Secret Display & Rotate.
 *
 * 마스킹 표시 + reveal(30s 카운트다운) + copy + rotate 액션.
 * 본 1차 스캐폴딩에서는 reveal/copy/rotate의 콜백을 외부로 노출만 하고
 * 백엔드 검증·audit 기록은 후속 이슈에서 연결한다.
 */

import { useEffect, useState } from "react";
import { Copy, Eye, EyeOff, RotateCw } from "lucide-react";
import { cn } from "@/lib/cn";
import { Button } from "@/components/atoms/Button";

export interface SecretFieldProps {
  /** 키 이름 — `keyring:claude_api_key` 등 사람이 읽는 식별자. */
  name: string;
  /** 표시용 마지막 4자리. mask는 컴포넌트 안에서 합성한다. */
  lastFour?: string;
  /** reveal 클릭 시 일시적으로 보여줄 평문 — 30s 후 자동 마스킹. */
  plaintext?: string;
  /** 외부 액션 콜백 — 본 컴포넌트는 표시·UX만 담당. */
  onReveal?: () => Promise<string | undefined> | string | undefined;
  onCopy?: () => void;
  onRotate?: () => void;
  className?: string;
}

const REVEAL_TTL_MS = 30_000;

export function SecretField({
  name,
  lastFour = "••••",
  plaintext,
  onReveal,
  onCopy,
  onRotate,
  className,
}: SecretFieldProps) {
  const [revealed, setRevealed] = useState<string | null>(null);

  // reveal TTL 자동 만료. DESIGN.md §4.2의 30초 카운트다운을 지킨다.
  useEffect(() => {
    if (!revealed) return;
    const t = setTimeout(() => setRevealed(null), REVEAL_TTL_MS);
    return () => clearTimeout(t);
  }, [revealed]);

  async function handleReveal() {
    if (revealed) {
      setRevealed(null);
      return;
    }
    if (onReveal) {
      const value = await onReveal();
      if (value) setRevealed(value);
      return;
    }
    if (plaintext) setRevealed(plaintext);
  }

  return (
    <div
      className={cn(
        "flex items-center gap-3 rounded-[--radius-m] border border-[--border] bg-[--card] px-3 py-2",
        className,
      )}
    >
      <code className="font-mono text-xs text-[--muted-foreground]">{name}</code>
      <span
        aria-label={revealed ? "노출된 시크릿" : "마스킹된 시크릿"}
        className={cn(
          "ml-auto flex items-center gap-1 rounded-[--radius-sm] px-2 py-1 font-mono text-xs",
          revealed
            ? "bg-[--color-warning-bg] text-[--color-warning]"
            : "bg-[--secret-mask-bg] text-[--foreground]",
        )}
      >
        {revealed ?? `••••${lastFour}`}
      </span>
      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="sm"
          aria-label={revealed ? "시크릿 가리기" : "시크릿 보기 (30s)"}
          onClick={handleReveal}
        >
          {revealed ? (
            <EyeOff size={14} aria-hidden />
          ) : (
            <Eye size={14} aria-hidden />
          )}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          aria-label="시크릿 복사"
          onClick={onCopy}
        >
          <Copy size={14} aria-hidden />
        </Button>
        <Button
          variant="ghost"
          size="sm"
          aria-label="시크릿 회전"
          onClick={onRotate}
        >
          <RotateCw size={14} aria-hidden />
        </Button>
      </div>
    </div>
  );
}
