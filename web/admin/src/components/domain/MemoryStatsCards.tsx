"use client";

/**
 * MemoryStatsCards — 대화 저장소 통계 3-카드(총 메시지·디스크·마지막 드리밍).
 *
 * sqlite3 CLI 미설치 환경에서는 totalMessages가 null로 들어온다 — UI는 "—"로 표시.
 */

import { Database, HardDrive, Sparkles } from "lucide-react";
import type { MemoryStats } from "@/lib/api/memory";

export function MemoryStatsCards({ stats }: { stats: MemoryStats }) {
  return (
    <section
      aria-label="대화 저장소 통계"
      className="grid grid-cols-1 gap-3 sm:grid-cols-3"
    >
      <StatCard
        icon={<Database size={14} aria-hidden />}
        label="총 메시지"
        value={
          stats.totalMessages == null
            ? "—"
            : stats.totalMessages.toLocaleString("ko-KR")
        }
        hint={
          stats.totalMessages == null
            ? "sqlite3 CLI 미설치 — 카운트를 가져오지 못했어요."
            : "conversations.db 기준"
        }
      />
      <StatCard
        icon={<HardDrive size={14} aria-hidden />}
        label="디스크 사용량"
        value={formatBytes(stats.diskBytes)}
        hint=".agent/conversations.db"
      />
      <StatCard
        icon={<Sparkles size={14} aria-hidden />}
        label="마지막 드리밍"
        value={stats.lastDreamingAt ? formatDateTime(stats.lastDreamingAt) : "—"}
        hint={stats.lastDreamingAt ? "MEMORY.md 수정 시각" : "아직 한 번도 안 돌았어요."}
      />
    </section>
  );
}

interface StatCardProps {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint?: string;
}

function StatCard({ icon, label, value, hint }: StatCardProps) {
  return (
    <div className="rounded-[--radius-l] border border-[--border] bg-[--card] p-4">
      <div className="flex items-center gap-2 text-xs text-[--muted-foreground]">
        {icon}
        <span>{label}</span>
      </div>
      <div className="mt-2 font-mono text-xl font-semibold text-[--foreground-strong]">
        {value}
      </div>
      {hint ? (
        <div className="mt-1 text-[11px] text-[--muted-foreground]">{hint}</div>
      ) : null}
    </div>
  );
}

function formatBytes(b: number): string {
  if (b <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = b;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v < 10 && i > 0 ? 2 : v < 100 && i > 0 ? 1 : 0)} ${units[i]}`;
}

function formatDateTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString("ko-KR", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
