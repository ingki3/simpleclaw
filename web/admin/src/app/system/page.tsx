"use client";

/**
 * System 화면 (BIZ-54) — admin.pen Screen 11 + admin-requirements.md §6.11.
 *
 * 구성(상→하, 좌→우):
 *   1. 헤더 (제목 + 새로고침)
 *   2. 시스템 정보 카드 — `/admin/v1/system/info` (version, build hash, PID, uptime,
 *      디스크, DB 경로) — 1회 fetch + 수동 새로고침.
 *   3. 재시작 액션 카드 — Daemon ⏻ / Process ⏻⏻. ConfirmGate(파일명/문구 일치) →
 *      RestartStepper(5단계)로 격상.
 *   4. 서브시스템 헬스 카드 — `/admin/v1/health` (5초 폴링) + `/admin/v1/config/llm`
 *      (LLM은 헬스 키가 없어 default provider 유효성으로 약식 판정).
 *   5. config.yaml 스냅샷 카드 — `/admin/v1/config` (시크릿 마스킹 적용된 채로 노출).
 *   6. 테마 카드 — 라이트/다크/시스템 라디오. 즉시 반영(↻ Hot).
 *
 * 디자인 결정:
 *  - 본 화면은 *진단 + 위험 액션* 화면이므로 SettingCard 기반 스택 레이아웃을 사용한다.
 *    LLM/Cron 같은 편집 화면과 달리 sticky DryRunFooter는 두지 않는다.
 *  - 재시작은 펜딩 변경 유무와 관계없이 운영자가 트리거할 수 있어야 한다(단, 헤더에
 *    펜딩 표시는 함께 노출).
 */

import { useCallback } from "react";
import { Cog, RefreshCw } from "lucide-react";
import { Button } from "@/components/atoms/Button";
import { useAdminResource } from "@/lib/api/use-admin-resource";
import type { HealthSnapshot, SystemInfoResponse } from "@/lib/api";

import { SystemInfoCard } from "./_components/SystemInfoCard";
import { RestartActions } from "./_components/RestartActions";
import { SubsystemHealthCard } from "./_components/SubsystemHealthCard";
import { ConfigDumpCard } from "./_components/ConfigDumpCard";
import { ThemeCard } from "./_components/ThemeCard";

interface ConfigResponse {
  config: Record<string, unknown>;
}

interface LLMConfigShape {
  default?: string;
  providers?: Record<string, { enabled?: boolean; model?: string }>;
}
interface AreaConfigResponse {
  area: string;
  config: LLMConfigShape;
}

export default function SystemPage() {
  const info = useAdminResource<SystemInfoResponse>("/admin/v1/system/info");
  // 헬스는 대시보드와 같은 주기(5초)로 폴링한다.
  const health = useAdminResource<HealthSnapshot>("/admin/v1/health", {
    intervalMs: 5_000,
  });
  const llm = useAdminResource<AreaConfigResponse>("/admin/v1/config/llm");
  const config = useAdminResource<ConfigResponse>("/admin/v1/config");

  const refreshAll = useCallback(() => {
    info.refetch();
    health.refetch();
    llm.refetch();
    config.refetch();
  }, [info, health, llm, config]);

  return (
    <div className="flex flex-col gap-6">
      <header className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <Cog
            size={28}
            strokeWidth={1.5}
            aria-hidden
            className="mt-1 text-[--primary]"
          />
          <div className="flex flex-col gap-1">
            <h1 className="text-2xl font-semibold text-[--foreground-strong]">
              시스템
            </h1>
            <p className="text-sm text-[--muted-foreground]">
              데몬 상태·재시작 액션·전체 설정 스냅샷·테마를 한 곳에서 다룹니다.
            </p>
          </div>
        </div>
        <Button
          variant="ghost"
          size="sm"
          leftIcon={<RefreshCw size={14} aria-hidden />}
          onClick={refreshAll}
        >
          모두 새로고침
        </Button>
      </header>

      <SystemInfoCard
        data={info.data}
        isLoading={info.isLoading}
        error={info.error?.message}
        onRefresh={info.refetch}
      />

      <RestartActions
        pendingChanges={!!health.data?.pending_changes}
        onRestartCompleted={refreshAll}
      />

      <SubsystemHealthCard
        health={health.data}
        healthError={health.error?.message}
        healthLoading={health.isLoading}
        llm={llm.data?.config}
        llmError={llm.error?.message}
        llmLoading={llm.isLoading}
        onRefresh={() => {
          health.refetch();
          llm.refetch();
        }}
      />

      <ConfigDumpCard
        config={config.data?.config}
        isLoading={config.isLoading}
        error={config.error?.message}
        onRefresh={config.refetch}
      />

      <ThemeCard />
    </div>
  );
}
