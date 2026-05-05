"use client";

/**
 * WebhookGuardCard — Domain. rate-limit·body·concurrency 슬라이더 + 트래픽 시뮬 (DESIGN.md §3.4).
 *
 * Webhook 가드(레이트 리밋 / 페이로드 크기 / 동시 처리) 3 슬라이더와
 * 현재 트래픽 시뮬레이션 결과를 한 카드에 박제.
 *
 * 본 컴포넌트는 *시각/UX 행위* 만 담당. 실제 트래픽 시뮬레이션 결과는 부모가 prop 으로 주입.
 */

import { cn } from "@/lib/cn";

export interface GuardSlider {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  /** 단위 표기 (예: "req/min", "KB"). */
  unit?: string;
  onChange: (next: number) => void;
}

export interface WebhookGuardCardProps {
  rateLimit: GuardSlider;
  bodySize: GuardSlider;
  concurrency: GuardSlider;
  /** 트래픽 시뮬 결과 — 보통 한 줄 요약(허용/차단 건수). */
  simulation?: React.ReactNode;
  className?: string;
}

function Slider({ slider }: { slider: GuardSlider }) {
  const { label, value, min, max, step = 1, unit, onChange } = slider;
  return (
    <label className="flex flex-col gap-1.5 text-sm">
      <span className="flex items-center justify-between text-(--foreground)">
        <span>{label}</span>
        <span className="tabular-nums text-(--muted-foreground)">
          {value}
          {unit ? ` ${unit}` : ""}
        </span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-(--primary)"
      />
    </label>
  );
}

export function WebhookGuardCard({
  rateLimit,
  bodySize,
  concurrency,
  simulation,
  className,
}: WebhookGuardCardProps) {
  return (
    <section
      className={cn(
        "flex flex-col gap-4 rounded-(--radius-l) border border-(--border) bg-(--card) p-6",
        className,
      )}
    >
      <header className="text-sm font-semibold text-(--foreground-strong)">
        Webhook Guard
      </header>
      <div className="flex flex-col gap-3">
        <Slider slider={rateLimit} />
        <Slider slider={bodySize} />
        <Slider slider={concurrency} />
      </div>
      {simulation ? (
        <footer className="rounded-(--radius-m) bg-(--surface) p-3 text-sm text-(--muted-foreground)">
          {simulation}
        </footer>
      ) : null}
    </section>
  );
}
