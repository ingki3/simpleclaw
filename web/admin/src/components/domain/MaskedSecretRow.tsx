"use client";

/**
 * MaskedSecretRow — DESIGN.md §3.2 / §4.2.
 *
 * SecretField를 테이블 한 행처럼 사용하기 위한 도메인 wrapper.
 * 좌측에 키 라벨 + 환경 배지를 함께 노출해 시크릿의 적용 범위를 명시한다.
 */

import { SecretField } from "@/components/molecules/SecretField";
import { Badge } from "@/components/atoms/Badge";

export interface MaskedSecretRowProps {
  /** 사람이 읽는 라벨 — 예: "Claude API Key". */
  label: string;
  /** 키링 식별자 — 예: "keyring:claude_api_key". */
  keyName: string;
  /** 적용 환경 — local/dev/prod. */
  scope: "local" | "dev" | "prod";
  lastFour?: string;
  onCopy?: () => void;
  onRotate?: () => void;
  onReveal?: () => Promise<string | undefined> | string | undefined;
}

export function MaskedSecretRow({
  label,
  keyName,
  scope,
  lastFour,
  onCopy,
  onRotate,
  onReveal,
}: MaskedSecretRowProps) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2 text-sm">
        <span className="font-medium text-[--foreground-strong]">{label}</span>
        <Badge tone={scope === "prod" ? "danger" : scope === "dev" ? "warning" : "neutral"}>
          {scope}
        </Badge>
      </div>
      <SecretField
        name={keyName}
        lastFour={lastFour}
        onCopy={onCopy}
        onRotate={onRotate}
        onReveal={onReveal}
      />
    </div>
  );
}
