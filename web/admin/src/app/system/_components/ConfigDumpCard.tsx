"use client";

/**
 * ConfigDumpCard — System 화면의 config.yaml 읽기 전용 덤프.
 *
 * `/admin/v1/config`는 시크릿 키 패턴 값을 자동 마스킹해 반환한다(채널 백엔드의
 * `_mask_secrets`). 본 컴포넌트는 추가 마스킹을 하지 않고, 운영자가 검토만 할 수
 * 있도록 코드 블록으로 노출한다.
 *
 * 디자인 결정:
 *  - 편집 액션은 의도적으로 제공하지 않는다. 개별 영역 편집은 LLM/Persona 등
 *    각 화면에서 PATCH로 수행한다 — 이 카드는 *전체 진단용 스냅샷*에 한정한다.
 *  - YAML 직렬화는 `js-yaml` 같은 의존성을 늘리지 않고, `JSON.stringify`로 충분히
 *    명확한 트리 표현이 가능하므로 JSON으로 보여준다(문자열·null·dict 모두 보존).
 */

import { useMemo, useState } from "react";
import { Copy, Check, RefreshCw } from "lucide-react";
import { Badge } from "@/components/atoms/Badge";
import { Button } from "@/components/atoms/Button";
import { SettingCard } from "@/components/molecules/SettingCard";

export interface ConfigDumpCardProps {
  config: Record<string, unknown> | undefined;
  isLoading: boolean;
  error: string | undefined;
  onRefresh: () => void;
}

export function ConfigDumpCard({
  config,
  isLoading,
  error,
  onRefresh,
}: ConfigDumpCardProps) {
  const [copied, setCopied] = useState(false);

  const text = useMemo(() => {
    if (!config) return "";
    try {
      return JSON.stringify(config, null, 2);
    } catch {
      return String(config);
    }
  }, [config]);

  async function handleCopy() {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1_500);
    } catch {
      // 클립보드 권한 거부는 조용히 무시 — 토스트 핸들러를 부모에 의존시키지 않기 위해.
    }
  }

  return (
    <SettingCard
      title="config.yaml 스냅샷"
      description="현재 머지된 설정의 읽기 전용 덤프입니다. 시크릿 키 값은 자동 마스킹됩니다."
      headerRight={
        <div className="flex items-center gap-2">
          <Badge tone="info">Read-only</Badge>
          <Button
            variant="ghost"
            size="sm"
            leftIcon={<RefreshCw size={14} aria-hidden />}
            onClick={onRefresh}
            disabled={isLoading}
          >
            새로고침
          </Button>
          <Button
            variant="outline"
            size="sm"
            leftIcon={
              copied ? (
                <Check size={14} aria-hidden />
              ) : (
                <Copy size={14} aria-hidden />
              )
            }
            onClick={handleCopy}
            disabled={!text}
          >
            {copied ? "복사됨" : "복사"}
          </Button>
        </div>
      }
    >
      {error ? (
        <p
          role="alert"
          className="rounded-[--radius-m] border border-[--color-error] bg-[--color-error-bg] p-3 text-sm text-[--color-error]"
        >
          설정을 가져오지 못했습니다: {error}
        </p>
      ) : !config ? (
        <p className="text-sm text-[--muted-foreground]">불러오는 중…</p>
      ) : (
        <pre
          aria-label="config.yaml 덤프 (시크릿 마스킹)"
          className="max-h-[28rem] overflow-auto rounded-[--radius-m] border border-[--border-divider] bg-[--surface] p-4 font-mono text-xs leading-relaxed text-[--foreground]"
        >
          {text}
        </pre>
      )}
    </SettingCard>
  );
}
