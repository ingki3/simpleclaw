"use client";

/**
 * ProviderCard — DESIGN.md §3.4 Domain.
 *
 * LLM 프로바이더(Claude / Gemini / OpenAI 등) 한 칸. 라우터에서의 활성 여부,
 * 모델 이름, 마지막 핑 결과를 한 번에 보여준다.
 */

import { CheckCircle2 } from "lucide-react";
import { Switch } from "@/components/atoms/Switch";
import { StatusPill, type StatusTone } from "@/components/atoms/StatusPill";
import { Badge } from "@/components/atoms/Badge";
import { cn } from "@/lib/cn";

export interface ProviderCardProps {
  name: string;
  /** 사람이 읽는 모델 식별자 — 예: "claude-opus-4-7". */
  model: string;
  enabled: boolean;
  /** 마지막 ping 결과. */
  health: { tone: StatusTone; label: string };
  /** 라우터 우선순위 — primary | fallback. */
  role?: "primary" | "fallback";
  onEnabledChange: (next: boolean) => void;
  className?: string;
}

export function ProviderCard({
  name,
  model,
  enabled,
  health,
  role = "primary",
  onEnabledChange,
  className,
}: ProviderCardProps) {
  return (
    <article
      className={cn(
        "flex flex-col gap-3 rounded-[--radius-l] border border-[--border] bg-[--card] p-5",
        className,
      )}
    >
      <header className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <h3 className="text-md font-semibold text-[--foreground-strong]">
              {name}
            </h3>
            {role === "primary" ? (
              <Badge tone="brand">
                <CheckCircle2 size={10} className="mr-1" aria-hidden /> Primary
              </Badge>
            ) : (
              <Badge tone="neutral">Fallback</Badge>
            )}
          </div>
          <code className="font-mono text-xs text-[--muted-foreground]">
            {model}
          </code>
        </div>
        <Switch
          checked={enabled}
          onCheckedChange={onEnabledChange}
          label={`${name} 활성`}
        />
      </header>
      <div className="flex items-center justify-between text-xs text-[--muted-foreground]">
        <StatusPill tone={health.tone}>{health.label}</StatusPill>
      </div>
    </article>
  );
}
