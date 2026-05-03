"use client";

/**
 * ProviderEditor — LLM 프로바이더 한 칸의 편집 폼.
 *
 * BIZ-45 §범위:
 *  - 활성 토글(``enabled``) — ♻ Hot-reload (다음 호출부터 라우터가 인식)
 *  - 모델 드롭다운(프로바이더별 화이트리스트) — ↻ Hot
 *  - 토큰 예산(``token_budget``) — ↻ Hot
 *  - 폴백 우선순위(``fallback_priority``) — ↻ Hot
 *  - API 키 마스킹/Reveal(5초)/Rotate
 *
 * 시각 토대는 디자인 시스템의 ``ProviderCard``를 차용하되 편집 입력을 추가한다.
 */

import { useId, useState } from "react";
import { CheckCircle2 } from "lucide-react";
import { Switch } from "@/components/atoms/Switch";
import { Badge } from "@/components/atoms/Badge";
import { StatusPill, type StatusTone } from "@/components/atoms/StatusPill";
import { Input } from "@/components/atoms/Input";
import { Button } from "@/components/atoms/Button";
import { SecretField } from "@/components/molecules/SecretField";
import { cn } from "@/lib/cn";
import type { ProviderConfig } from "@/lib/api/llm";
import { revealSecret, rotateSecret } from "@/lib/api/llm";
import { useToast } from "@/lib/toast";

export interface ProviderEditorProps {
  name: string;
  /** 현재 폼 상태(편집 중인 값). */
  value: ProviderConfig;
  /** 프로바이더별 모델 후보 — 기본 화이트리스트. 운영자는 자유 입력도 가능. */
  modelOptions: string[];
  /** 마지막 ping/health 결과. */
  health: { tone: StatusTone; label: string };
  /** primary | fallback — ``llm.default``와 일치 여부 + 우선순위로 결정. */
  role?: "primary" | "fallback";
  onChange: (next: ProviderConfig) => void;
  /** 키 회전 성공 시 호출 — 페이지가 시크릿 메타를 다시 fetch한다. */
  onSecretRotated?: () => void;
  className?: string;
}

/** 시크릿 참조 문자열에서 백엔드/이름을 분리. ``keyring:foo`` → {backend:keyring, name:foo}. */
function parseSecretRef(ref: string | undefined): { backend?: string; name?: string } {
  if (!ref) return {};
  const m = /^(env|keyring|file):(.+)$/.exec(ref);
  if (!m) return { name: ref };
  return { backend: m[1], name: m[2] };
}

export function ProviderEditor({
  name,
  value,
  modelOptions,
  health,
  role = "primary",
  onChange,
  onSecretRotated,
  className,
}: ProviderEditorProps) {
  const toast = useToast();
  const enabled = value.enabled !== false;
  const modelId = useId();
  const budgetId = useId();
  const priorityId = useId();
  const [rotating, setRotating] = useState(false);
  const [newSecret, setNewSecret] = useState("");
  const [showRotate, setShowRotate] = useState(false);

  const secretRef = value.api_key ?? "";
  const { backend, name: secretName } = parseSecretRef(secretRef);

  function patch(next: Partial<ProviderConfig>) {
    onChange({ ...value, ...next });
  }

  async function handleReveal(): Promise<string | undefined> {
    if (!secretName) {
      toast.push({
        tone: "info",
        title: "Reveal 불가",
        description: "API 키가 시크릿 참조 형식이 아닙니다 (예: keyring:claude_api_key).",
      });
      return undefined;
    }
    try {
      const res = await revealSecret(secretName, backend);
      return res.value;
    } catch (err) {
      toast.push({
        tone: "error",
        title: "Reveal 실패",
        description: err instanceof Error ? err.message : String(err),
      });
      return undefined;
    }
  }

  async function handleRotate() {
    if (!secretName) {
      toast.push({
        tone: "info",
        title: "Rotate 불가",
        description: "API 키가 시크릿 참조 형식이 아닙니다.",
      });
      return;
    }
    if (!newSecret) return;
    setRotating(true);
    try {
      await rotateSecret(secretName, newSecret, backend);
      toast.push({
        tone: "success",
        title: `${name} API 키를 회전했습니다`,
        description: `백엔드: ${backend ?? "auto"}. 마지막 4자리는 메타데이터에서 확인하세요.`,
      });
      setNewSecret("");
      setShowRotate(false);
      onSecretRotated?.();
    } catch (err) {
      toast.push({
        tone: "error",
        title: "Rotate 실패",
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setRotating(false);
    }
  }

  return (
    <article
      className={cn(
        "flex flex-col gap-4 rounded-[--radius-l] border border-[--border] bg-[--card] p-5",
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
            <Badge tone="info">♻ Hot-reload</Badge>
          </div>
          <code className="font-mono text-xs text-[--muted-foreground]">
            type={value.type ?? "api"}
          </code>
        </div>
        <div className="flex items-center gap-2">
          <StatusPill tone={health.tone}>{health.label}</StatusPill>
          <Switch
            checked={enabled}
            onCheckedChange={(next) => patch({ enabled: next })}
            label={`${name} 활성`}
          />
        </div>
      </header>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <div className="flex flex-col gap-1">
          <label htmlFor={modelId} className="text-xs font-medium text-[--muted-foreground]">
            모델
          </label>
          <select
            id={modelId}
            value={value.model ?? ""}
            onChange={(e) => patch({ model: e.target.value })}
            className="rounded-[--radius-m] border border-[--border] bg-[--card] px-3 py-2 text-sm text-[--foreground] focus:border-[--primary] focus:outline-none"
          >
            <option value="">— 선택 —</option>
            {modelOptions.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
            {value.model && !modelOptions.includes(value.model) ? (
              <option value={value.model}>{value.model} (사용자 지정)</option>
            ) : null}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor={budgetId} className="text-xs font-medium text-[--muted-foreground]">
            토큰 예산 (월)
          </label>
          <Input
            id={budgetId}
            type="number"
            inputMode="numeric"
            min={0}
            placeholder="무제한"
            value={value.token_budget ?? ""}
            onChange={(e) => {
              const raw = e.target.value;
              if (raw === "") {
                const next = { ...value };
                delete next.token_budget;
                onChange(next);
                return;
              }
              const n = Number(raw);
              if (Number.isFinite(n) && n >= 0) patch({ token_budget: n });
            }}
          />
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor={priorityId} className="text-xs font-medium text-[--muted-foreground]">
            폴백 우선순위
          </label>
          <Input
            id={priorityId}
            type="number"
            inputMode="numeric"
            min={0}
            placeholder="0 (높음)"
            value={value.fallback_priority ?? ""}
            onChange={(e) => {
              const raw = e.target.value;
              if (raw === "") {
                const next = { ...value };
                delete next.fallback_priority;
                onChange(next);
                return;
              }
              const n = Number(raw);
              if (Number.isFinite(n) && n >= 0) patch({ fallback_priority: n });
            }}
          />
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-xs font-medium text-[--muted-foreground]">
          API 키
        </span>
        <SecretField
          name={secretRef || `${name} api_key`}
          lastFour={secretName ? secretName.slice(-4) : "????"}
          onReveal={handleReveal}
          onRotate={() => setShowRotate((prev) => !prev)}
          revealTtlMs={5_000}
        />
        {showRotate ? (
          <div className="flex flex-col gap-2 rounded-[--radius-m] border border-dashed border-[--border-strong] bg-[--surface] p-3">
            <Input
              type="password"
              autoComplete="off"
              placeholder="새 API 키"
              value={newSecret}
              onChange={(e) => setNewSecret(e.target.value)}
            />
            <div className="flex justify-end gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setShowRotate(false);
                  setNewSecret("");
                }}
              >
                취소
              </Button>
              <Button
                variant="primary"
                size="sm"
                disabled={!newSecret || rotating}
                onClick={handleRotate}
              >
                {rotating ? "회전 중…" : "Rotate"}
              </Button>
            </div>
          </div>
        ) : null}
      </div>
    </article>
  );
}
