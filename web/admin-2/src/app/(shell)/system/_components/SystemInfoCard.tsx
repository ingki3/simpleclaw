/**
 * SystemInfoCard — admin.pen `t9bQD` (cardSysInfo) 박제.
 *
 * 5 행 key/value 메타: 버전 · 빌드 · 호스트 · Uptime · 환경(Badge).
 * key 는 muted, value 는 mono 폰트로 정렬. 환경만 Badge 컴포넌트로 표시.
 */
"use client";

import type { ReactNode } from "react";
import { Badge } from "@/design/atoms/Badge";
import { cn } from "@/lib/cn";
import type { SystemInfo } from "../_data";

interface SystemInfoCardProps {
  info: SystemInfo;
  className?: string;
}

export function SystemInfoCard({ info, className }: SystemInfoCardProps) {
  return (
    <section
      data-testid="system-info-card"
      aria-label="시스템 정보"
      className={cn(SECTION_CLASS, className)}
    >
      <h2 className="text-sm font-semibold text-(--foreground-strong)">
        시스템 정보
      </h2>
      <Row label="버전" value={info.version} />
      <Row label="빌드" value={info.build} />
      <Row label="호스트" value={info.host} />
      <Row label="Uptime" value={info.uptime} />
      <Row
        label="환경"
        value={
          <Badge tone="brand" data-testid="system-info-environment">
            {info.environment}
          </Badge>
        }
      />
    </section>
  );
}

const SECTION_CLASS =
  "flex flex-col gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-5 shadow-(--shadow-sm)";

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 text-xs">
      <span className="text-(--muted-foreground)">{label}</span>
      {typeof value === "string" ? (
        <span className="font-mono text-(--foreground)">{value}</span>
      ) : (
        value
      )}
    </div>
  );
}
