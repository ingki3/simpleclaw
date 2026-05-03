"use client";

/**
 * SystemInfoCard — System 화면 좌상단 카드.
 *
 * `/admin/v1/system/info`의 정적 진단 정보(version, build hash, PID, uptime,
 * config/db path, disk 사용량)를 한 카드에 정렬해 노출한다. 새로고침은 호출자가
 * `refetch`로 트리거하며, 본 컴포넌트는 로딩/에러/정상 3분기만 책임진다.
 */

import { useMemo } from "react";
import { RefreshCw } from "lucide-react";
import type { SystemInfoResponse } from "@/lib/api";
import { Button } from "@/components/atoms/Button";
import { SettingCard } from "@/components/molecules/SettingCard";
import { formatBytes, formatUptime, percent } from "./format";

export interface SystemInfoCardProps {
  data: SystemInfoResponse | undefined;
  isLoading: boolean;
  error: string | undefined;
  onRefresh: () => void;
}

export function SystemInfoCard({
  data,
  isLoading,
  error,
  onRefresh,
}: SystemInfoCardProps) {
  // disk usage 비율 — 90%↑이면 경고 톤. 데이터 부재 시 null.
  const diskRatio = useMemo(() => {
    if (!data?.disk || data.disk.total_bytes <= 0) return null;
    return data.disk.used_bytes / data.disk.total_bytes;
  }, [data]);

  return (
    <SettingCard
      title="시스템 정보"
      description="버전·프로세스 정보·데이터 위치를 한눈에 확인합니다."
      headerRight={
        <Button
          variant="ghost"
          size="sm"
          leftIcon={<RefreshCw size={14} aria-hidden />}
          onClick={onRefresh}
          disabled={isLoading}
        >
          새로고침
        </Button>
      }
    >
      {error ? (
        <p
          role="alert"
          className="rounded-[--radius-m] border border-[--color-error] bg-[--color-error-bg] p-3 text-sm text-[--color-error]"
        >
          시스템 정보를 가져오지 못했습니다: {error}
        </p>
      ) : !data ? (
        <p className="text-sm text-[--muted-foreground]">불러오는 중…</p>
      ) : (
        <dl className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
          <Row label="버전" value={data.version} />
          <Row label="빌드 해시" value={data.build_sha ?? "—"} mono />
          <Row label="Python" value={data.python_version} />
          <Row label="플랫폼" value={data.platform} />
          <Row label="PID" value={String(data.pid)} mono />
          <Row label="가동 시간" value={formatUptime(data.uptime_seconds)} />
          <Row label="Bind" value={`${data.host}:${data.port}`} mono />
          <Row label="config.yaml" value={data.config_path} mono />
          <Row
            label="DB 경로"
            value={data.db_path}
            mono
            hint={
              data.db_exists
                ? data.db_size_bytes != null
                  ? `${formatBytes(data.db_size_bytes)} (존재)`
                  : "존재"
                : "파일 없음"
            }
            hintTone={data.db_exists ? "muted" : "warn"}
          />
          {data.disk ? (
            <Row
              label="디스크"
              value={`${formatBytes(data.disk.free_bytes)} 여유 / ${formatBytes(data.disk.total_bytes)}`}
              hint={
                diskRatio != null
                  ? `사용 ${percent(diskRatio)} · ${data.disk.path}`
                  : data.disk.path
              }
              hintTone={
                diskRatio != null && diskRatio >= 0.9 ? "warn" : "muted"
              }
            />
          ) : (
            <Row label="디스크" value="—" hint="OS에서 정보를 얻지 못했습니다." />
          )}
        </dl>
      )}
    </SettingCard>
  );
}

interface RowProps {
  label: string;
  value: string;
  /** 모노스페이스 + 잘림 제어 — 경로/해시류에 사용. */
  mono?: boolean;
  /** 한 줄 보조 설명. */
  hint?: string;
  hintTone?: "muted" | "warn";
}

function Row({ label, value, mono, hint, hintTone = "muted" }: RowProps) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs uppercase tracking-wide text-[--muted-foreground]">
        {label}
      </dt>
      <dd
        className={
          mono
            ? "break-all font-mono text-sm text-[--foreground-strong]"
            : "text-sm text-[--foreground-strong]"
        }
      >
        {value}
      </dd>
      {hint ? (
        <p
          className={
            hintTone === "warn"
              ? "text-xs text-[--color-warning]"
              : "text-xs text-[--muted-foreground]"
          }
        >
          {hint}
        </p>
      ) : null}
    </div>
  );
}
