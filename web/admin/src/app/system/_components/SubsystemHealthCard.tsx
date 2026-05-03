"use client";

/**
 * SubsystemHealthCard — System 화면의 서브시스템 헬스 점검 카드.
 *
 * `/admin/v1/health` 응답에서 daemon / channels / memory / cron 영역의 ping 결과를
 * 추려 1줄씩 표시한다. LLM은 헬스 응답에 자체 키가 없으므로
 * `/admin/v1/config/llm`의 default 프로바이더 존재 여부로 약식 판정한다.
 *
 * 5초 폴링은 `useAdminResource`가 처리하며, 본 컴포넌트는 표시만 책임진다.
 */

import { useMemo } from "react";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/atoms/Button";
import { StatusPill, type StatusTone } from "@/components/atoms/StatusPill";
import { SettingCard } from "@/components/molecules/SettingCard";
import type { HealthSnapshot } from "@/lib/api";

interface LLMConfigShape {
  default?: string;
  providers?: Record<string, { enabled?: boolean; model?: string }>;
}

export interface SubsystemHealthCardProps {
  health: HealthSnapshot | undefined;
  healthError: string | undefined;
  healthLoading: boolean;
  llm: LLMConfigShape | undefined;
  llmError: string | undefined;
  llmLoading: boolean;
  onRefresh: () => void;
}

interface CheckRow {
  name: string;
  tone: StatusTone;
  label: string;
  detail: string;
}

export function SubsystemHealthCard({
  health,
  healthError,
  healthLoading,
  llm,
  llmError,
  llmLoading,
  onRefresh,
}: SubsystemHealthCardProps) {
  const rows = useMemo<CheckRow[]>(() => {
    const out: CheckRow[] = [];

    // Daemon — health.status === "ok" + uptime 노출.
    out.push(daemonRow(health, healthError));

    // Channels — health.channels.{active,total}.
    out.push(channelsRow(health, healthError));

    // Memory — health.metrics.memory_* / health.memory.*. 키 부재 시 "메트릭 없음".
    out.push(memoryRow(health, healthError));

    // Cron — health.cron.{active,total}.
    out.push(cronRow(health, healthError));

    // LLM — config 응답을 약식 health proxy로 사용.
    out.push(llmRow(llm, llmError, llmLoading));

    return out;
  }, [health, healthError, healthLoading, llm, llmError, llmLoading]);

  return (
    <SettingCard
      title="서브시스템 헬스"
      description="각 영역의 ping/health 결과를 5초 주기로 갱신합니다."
      headerRight={
        <Button
          variant="ghost"
          size="sm"
          leftIcon={<RefreshCw size={14} aria-hidden />}
          onClick={onRefresh}
          disabled={healthLoading}
        >
          지금 확인
        </Button>
      }
    >
      <ul className="flex flex-col gap-2">
        {rows.map((row) => (
          <li
            key={row.name}
            className="flex items-center justify-between gap-3 rounded-[--radius-m] border border-[--border] bg-[--surface] px-3 py-2"
          >
            <div className="flex min-w-0 flex-col">
              <span className="text-sm font-medium text-[--foreground-strong]">
                {row.name}
              </span>
              <span className="truncate text-xs text-[--muted-foreground]">
                {row.detail}
              </span>
            </div>
            <StatusPill tone={row.tone}>{row.label}</StatusPill>
          </li>
        ))}
      </ul>
    </SettingCard>
  );
}

// ---------------------------------------------------------------------------
// Row builders — 각 서브시스템마다 헬스 응답에서 의미있는 표현을 골라 정규화.
// ---------------------------------------------------------------------------

function daemonRow(
  h: HealthSnapshot | undefined,
  error: string | undefined,
): CheckRow {
  if (error) return { name: "Daemon", tone: "error", label: "에러", detail: error };
  if (!h) return { name: "Daemon", tone: "neutral", label: "확인 중", detail: "헬스 응답 대기" };
  const state =
    (h as { daemon?: { state?: string } }).daemon?.state ??
    (h.status === "ok" ? "running" : undefined);
  const tone: StatusTone =
    state === "running"
      ? "success"
      : state === "idle"
        ? "info"
        : state === "stopped"
          ? "error"
          : "neutral";
  const label = state ?? "unknown";
  return {
    name: "Daemon",
    tone,
    label,
    detail: `uptime ${h.uptime_seconds ?? 0}s · pending ${h.pending_changes ? "있음" : "없음"}`,
  };
}

function channelsRow(
  h: HealthSnapshot | undefined,
  error: string | undefined,
): CheckRow {
  if (error) return row("Channels", "error", "에러", error);
  if (!h) return row("Channels", "neutral", "확인 중", "헬스 응답 대기");
  const ch = (h as { channels?: { active?: number; total?: number } }).channels;
  if (!ch || ch.active == null) {
    return row("Channels", "neutral", "메트릭 없음", "daemon 헬스 익스텐션 미주입");
  }
  const tone: StatusTone =
    ch.active > 0 ? "success" : ch.total && ch.total > 0 ? "warning" : "neutral";
  return row(
    "Channels",
    tone,
    `${ch.active}${ch.total != null ? `/${ch.total}` : ""} 활성`,
    ch.active > 0 ? "텔레그램·웹훅 등 채널이 살아 있습니다." : "활성 채널이 없습니다.",
  );
}

function memoryRow(
  h: HealthSnapshot | undefined,
  error: string | undefined,
): CheckRow {
  if (error) return row("Memory", "error", "에러", error);
  if (!h) return row("Memory", "neutral", "확인 중", "헬스 응답 대기");
  const mem = (h as { memory?: { state?: string; messages?: number } }).memory;
  if (!mem) {
    const last24 = (h as { messages?: { last_24h?: number } }).messages?.last_24h;
    if (last24 == null) {
      return row("Memory", "neutral", "메트릭 없음", "memory 헬스 익스텐션 미주입");
    }
    return row("Memory", "info", `24h ${last24}건`, "messages.last_24h 메트릭만 노출됩니다.");
  }
  const tone: StatusTone = mem.state === "ok" ? "success" : mem.state === "degraded" ? "warning" : "info";
  return row(
    "Memory",
    tone,
    mem.state ?? "ok",
    mem.messages != null ? `누적 ${mem.messages}건` : "상태 보고됨",
  );
}

function cronRow(
  h: HealthSnapshot | undefined,
  error: string | undefined,
): CheckRow {
  if (error) return row("Cron", "error", "에러", error);
  if (!h) return row("Cron", "neutral", "확인 중", "헬스 응답 대기");
  const cron = (h as { cron?: { active?: number; total?: number } }).cron;
  if (!cron || cron.active == null) {
    return row("Cron", "neutral", "메트릭 없음", "daemon 헬스 익스텐션 미주입");
  }
  const tone: StatusTone =
    cron.active > 0 ? "success" : cron.total && cron.total > 0 ? "warning" : "neutral";
  return row(
    "Cron",
    tone,
    `${cron.active}${cron.total != null ? `/${cron.total}` : ""} 활성`,
    cron.active > 0 ? "스케줄러 정상 작동 중입니다." : "예약된 작업이 없습니다.",
  );
}

function llmRow(
  llm: LLMConfigShape | undefined,
  error: string | undefined,
  loading: boolean,
): CheckRow {
  if (error) return row("LLM", "error", "에러", error);
  if (loading && !llm) return row("LLM", "neutral", "확인 중", "config 응답 대기");
  if (!llm) return row("LLM", "neutral", "미설정", "프로바이더 정보를 가져오지 못했습니다.");
  const def = llm.default;
  const provider = def ? llm.providers?.[def] : undefined;
  if (!def) return row("LLM", "warning", "default 미지정", "기본 프로바이더가 설정되지 않았습니다.");
  if (!provider) return row("LLM", "warning", "프로바이더 누락", `default=${def}에 매칭되는 항목이 없습니다.`);
  if (provider.enabled === false) return row("LLM", "warning", `${def} 비활성`, "default 프로바이더가 비활성 상태입니다.");
  if (!provider.model) return row("LLM", "warning", `${def} 모델 미설정`, "model 키가 비어 있습니다.");
  return row("LLM", "success", `${def} · ${provider.model}`, "default 프로바이더가 사용 가능합니다.");
}

function row(
  name: string,
  tone: StatusTone,
  label: string,
  detail: string,
): CheckRow {
  return { name, tone, label, detail };
}
