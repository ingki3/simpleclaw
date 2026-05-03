"use client";

/**
 * LLM Router 화면 (BIZ-45) — admin.pen Screen 02 + admin-requirements.md §1·§5.
 *
 * 구조:
 *  1. 페이지 헤더 (제목 + 설명 + Reload)
 *  2. ProviderEditor 카드 그리드 (Claude/Gemini/OpenAI 등) — 활성 토글, 모델
 *     드롭다운, 토큰 예산, 폴백 우선순위, API 키 마스킹/Reveal(5s)/Rotate
 *  3. 카테고리 라우팅 테이블 (general/coding/reasoning/tools)
 *  4. Default 라우터(폴백 시 사용할 프로바이더)
 *  5. Dry-run diff 패널
 *  6. Sticky DryRunFooter — 취소 / Dry-run / 적용
 *
 * 적용 정책:
 *  - dry-run을 거쳐야만 ``Apply``가 활성화된다 (admin-requirements §2.3).
 *  - 적용 성공 시 토스트 + 5분 Undo 윈도 (admin-requirements §4.2).
 *  - Reveal은 5초 후 자동 마스킹 (issue BIZ-45 §범위).
 */

import { useEffect, useMemo, useState } from "react";
import { Brain, RefreshCw } from "lucide-react";
import { Button } from "@/components/atoms/Button";
import { Badge } from "@/components/atoms/Badge";
import { SettingCard } from "@/components/molecules/SettingCard";
import { DryRunFooter } from "@/components/molecules/DryRunFooter";
import { RestartBanner } from "@/components/molecules/RestartBanner";
import { ProviderEditor } from "@/components/llm/ProviderEditor";
import { RoutingTable } from "@/components/llm/RoutingTable";
import { DryRunDiff } from "@/components/llm/DryRunDiff";
import { useToast } from "@/lib/toast";
import {
  applyLLMPatch,
  dryRunLLMPatch,
  getLLMConfig,
  listSecrets,
  undoAudit,
  type DryRunResponse,
  type LLMConfig,
  type ProviderConfig,
  type RoutingMap,
  type SecretMeta,
} from "@/lib/api/llm";
import type { StatusTone } from "@/components/atoms/StatusPill";

// 프로바이더별 모델 화이트리스트 — admin-requirements §1.1번. 하드코딩이지만
// 운영자가 자유 입력할 수 있도록 ProviderEditor가 fallback 처리한다. 새 모델 출시
// 시에는 이 카탈로그를 갱신한다.
const MODEL_OPTIONS: Record<string, string[]> = {
  claude: [
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
  ],
  openai: ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"],
  gemini: ["gemini-3-flash-preview", "gemini-2.5-pro", "gemini-2.5-flash"],
};

// secrets 메타데이터에서 한 프로바이더의 ``api_key`` 후보를 찾는 휴리스틱.
function findSecretMeta(
  secrets: SecretMeta[],
  apiKeyRef: string | undefined,
): SecretMeta | undefined {
  if (!apiKeyRef) return undefined;
  const m = /^(env|keyring|file):(.+)$/.exec(apiKeyRef);
  const name = m ? m[2] : apiKeyRef;
  return secrets.find((s) => s.name === name);
}

function deriveProviderHealth(
  meta: SecretMeta | undefined,
  cfg: ProviderConfig | undefined,
): { tone: StatusTone; label: string } {
  if (!cfg) return { tone: "neutral", label: "미정" };
  if (cfg.enabled === false) return { tone: "neutral", label: "비활성" };
  if (!cfg.model) return { tone: "warning", label: "모델 미설정" };
  if (!meta) return { tone: "warning", label: "키 미등록" };
  return { tone: "success", label: "정상" };
}

// 섀도 비교 — JSON 문자열 비교로 충분하다(폼은 항상 동일 키 순서로 들어옴).
function isEqual(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

export default function LLMPage() {
  const toast = useToast();
  const [loading, setLoading] = useState(true);
  const [serverConfig, setServerConfig] = useState<LLMConfig | null>(null);
  const [draft, setDraft] = useState<LLMConfig | null>(null);
  const [secrets, setSecrets] = useState<SecretMeta[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [dryRunResult, setDryRunResult] = useState<DryRunResponse | null>(null);
  const [dryRunPending, setDryRunPending] = useState(false);
  const [applying, setApplying] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [cfg, secs] = await Promise.all([getLLMConfig(), listSecrets()]);
      // 서버는 ``providers``의 enabled 키를 보장하지 않는다 — UI에서 명시적으로
      // 기본 true 값으로 채워 토글이 의미를 갖게 한다.
      const normalized: LLMConfig = {
        default: cfg.default,
        providers: Object.fromEntries(
          Object.entries(cfg.providers ?? {}).map(([k, v]) => [
            k,
            { enabled: true, ...v },
          ]),
        ),
        routing: cfg.routing ?? {},
      };
      setServerConfig(normalized);
      setDraft(normalized);
      setSecrets(secs);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const dirty = useMemo(
    () => !!(serverConfig && draft && !isEqual(serverConfig, draft)),
    [serverConfig, draft],
  );

  // dirty가 풀리면 dry-run 결과를 무효화 (apply 잠금 복구).
  useEffect(() => {
    if (!dirty) {
      setDryRunResult(null);
    }
  }, [dirty]);

  const providers = useMemo(() => {
    if (!draft) return [] as Array<[string, ProviderConfig]>;
    return Object.entries(draft.providers ?? {});
  }, [draft]);

  // diff 패치 — 서버 상태와 비교해 ``providers``/``default``/``routing``의
  // 실제로 바뀐 키만 보낸다. 백엔드는 깊은 머지를 한다.
  function buildPatch(): Partial<LLMConfig> | null {
    if (!serverConfig || !draft) return null;
    const patch: Partial<LLMConfig> = {};
    if (draft.default !== serverConfig.default) {
      patch.default = draft.default;
    }
    const draftProviders = draft.providers ?? {};
    const serverProviders = serverConfig.providers ?? {};
    const providerPatch: Record<string, ProviderConfig> = {};
    for (const [name, cfg] of Object.entries(draftProviders)) {
      if (!isEqual(cfg, serverProviders[name])) {
        providerPatch[name] = cfg;
      }
    }
    if (Object.keys(providerPatch).length > 0) {
      patch.providers = providerPatch;
    }
    if (!isEqual(draft.routing ?? {}, serverConfig.routing ?? {})) {
      patch.routing = draft.routing ?? {};
    }
    if (Object.keys(patch).length === 0) return null;
    return patch;
  }

  async function handleDryRun() {
    const patch = buildPatch();
    if (!patch) {
      toast.push({ tone: "info", title: "변경 사항이 없습니다." });
      return;
    }
    setDryRunPending(true);
    try {
      const result = await dryRunLLMPatch(patch);
      setDryRunResult(result);
    } catch (err) {
      setDryRunResult(null);
      toast.push({
        tone: "error",
        title: "Dry-run 실패",
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setDryRunPending(false);
    }
  }

  async function handleApply() {
    const patch = buildPatch();
    if (!patch) return;
    setApplying(true);
    try {
      const result = await applyLLMPatch(patch);
      toast.push({
        tone: "success",
        title:
          result.outcome === "applied"
            ? "LLM 라우터 변경을 적용했습니다."
            : "변경이 펜딩되었습니다 — 데몬 재시작 후 적용됩니다.",
        description: result.policy
          ? `정책: ${result.policy.level} · 영향: ${result.policy.affected_modules.join(", ") || "—"}`
          : undefined,
        onUndo:
          result.outcome === "applied"
            ? async () => {
                try {
                  await undoAudit(result.audit_id);
                  await load();
                  toast.push({
                    tone: "info",
                    title: "변경을 되돌렸습니다.",
                  });
                } catch (err) {
                  toast.push({
                    tone: "error",
                    title: "Undo 실패",
                    description:
                      err instanceof Error ? err.message : String(err),
                  });
                }
              }
            : undefined,
      });
      setDryRunResult(null);
      await load();
    } catch (err) {
      toast.push({
        tone: "error",
        title: "적용 실패",
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setApplying(false);
    }
  }

  function handleCancel() {
    setDraft(serverConfig);
    setDryRunResult(null);
  }

  function updateProvider(name: string, next: ProviderConfig) {
    setDraft((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        providers: { ...(prev.providers ?? {}), [name]: next },
      };
    });
  }

  function updateRouting(next: RoutingMap) {
    setDraft((prev) => (prev ? { ...prev, routing: next } : prev));
  }

  function updateDefault(next: string) {
    setDraft((prev) => (prev ? { ...prev, default: next } : prev));
  }

  // 펜딩 변경 카운트 — restart banner 트리거. dry-run 결과의 정책 등급이
  // process-restart면 1로, 아니면 0으로 표시(단순화).
  const pendingRestart =
    dryRunResult?.policy.level === "Process-restart" ? 1 : 0;

  return (
    <div className="flex flex-col gap-6 pb-32">
      <header className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <Brain
            size={28}
            strokeWidth={1.5}
            aria-hidden
            className="mt-1 text-[--primary]"
          />
          <div className="flex flex-col gap-1">
            <h1 className="text-2xl font-semibold text-[--foreground-strong]">
              LLM 라우터
            </h1>
            <p className="text-sm text-[--muted-foreground]">
              프로바이더 활성·모델·토큰 예산·폴백·카테고리 라우팅을 한 화면에서
              관리합니다. 시크릿은 기본 마스킹되며 Reveal은 5초 후 자동 마스킹됩니다.
            </p>
          </div>
        </div>
        <Button
          variant="ghost"
          size="sm"
          leftIcon={<RefreshCw size={14} aria-hidden />}
          onClick={load}
          disabled={loading}
        >
          새로고침
        </Button>
      </header>

      <RestartBanner
        pending={pendingRestart}
        onRestartNow={() =>
          toast.push({
            tone: "info",
            title: "데몬 재시작이 필요합니다.",
            description: "시스템 화면(/system)에서 재시작 절차를 진행하세요.",
          })
        }
        onDeferUntilNextStart={() =>
          toast.push({
            tone: "info",
            title: "다음 데몬 시작 시 적용됩니다.",
          })
        }
      />

      {error ? (
        <div
          role="alert"
          className="rounded-[--radius-m] border border-[--color-error] bg-[--color-error-bg] p-3 text-sm text-[--color-error]"
        >
          설정을 불러오는 중 오류가 발생했습니다: {error}
        </div>
      ) : null}

      {loading || !draft ? (
        <div className="rounded-[--radius-m] border border-dashed border-[--border-divider] bg-[--surface] p-6 text-sm text-[--muted-foreground]">
          설정을 불러오는 중…
        </div>
      ) : (
        <>
          <SettingCard
            title="프로바이더"
            description="활성 토글·모델·토큰 예산·폴백 우선순위·API 키 회전을 카드별로 편집합니다."
            headerRight={<Badge tone="info">♻ Hot-reload</Badge>}
          >
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              {providers.length === 0 ? (
                <p className="text-sm text-[--muted-foreground]">
                  등록된 프로바이더가 없습니다. config.yaml에 ``llm.providers``를
                  추가한 뒤 새로고침하세요.
                </p>
              ) : (
                providers.map(([name, cfg]) => {
                  const isPrimary = name === draft.default;
                  const meta = findSecretMeta(secrets, cfg.api_key);
                  const opts = MODEL_OPTIONS[name] ?? [];
                  return (
                    <ProviderEditor
                      key={name}
                      name={name}
                      value={cfg}
                      modelOptions={opts}
                      role={isPrimary ? "primary" : "fallback"}
                      health={deriveProviderHealth(meta, cfg)}
                      onChange={(next) => updateProvider(name, next)}
                      onSecretRotated={load}
                    />
                  );
                })
              )}
            </div>
          </SettingCard>

          <SettingCard
            title="기본 라우터(default)"
            description="카테고리에 매핑되지 않은 호출은 이 프로바이더로 보냅니다."
            headerRight={<Badge tone="success">↻ Hot</Badge>}
          >
            <div className="flex flex-col gap-2 md:flex-row md:items-center md:gap-4">
              <label
                htmlFor="llm-default"
                className="text-sm font-medium text-[--foreground]"
              >
                Default provider
              </label>
              <select
                id="llm-default"
                value={draft.default ?? ""}
                onChange={(e) => updateDefault(e.target.value)}
                className="rounded-[--radius-m] border border-[--border] bg-[--card] px-3 py-2 text-sm text-[--foreground] focus:border-[--primary] focus:outline-none"
              >
                <option value="">— 선택 —</option>
                {providers.map(([name]) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </div>
          </SettingCard>

          <SettingCard
            title="카테고리 라우팅"
            description="작업 카테고리별로 사용할 프로바이더를 지정합니다. 비어 있으면 default를 사용합니다."
            headerRight={<Badge tone="success">↻ Hot</Badge>}
          >
            <RoutingTable
              value={draft.routing ?? {}}
              providers={providers.map(([n]) => n)}
              fallback={draft.default}
              onChange={updateRouting}
            />
          </SettingCard>

          <SettingCard
            title="Dry-run 검증"
            description="변경을 적용하기 전, 영향받는 키와 정책 등급을 미리 확인합니다."
          >
            <DryRunDiff result={dryRunResult} loading={dryRunPending} />
          </SettingCard>
        </>
      )}

      <div className="fixed bottom-0 left-60 right-0 z-30 border-t border-[--border-divider] bg-[--card] px-8 py-3 shadow-[--shadow-m]">
        <DryRunFooter
          dirty={dirty}
          dryRunPassed={!!dryRunResult && dirty && !applying}
          summary={
            dryRunResult
              ? `정책: ${dryRunResult.policy.level} · ${dryRunResult.policy.matched_keys.length} keys`
              : undefined
          }
          onCancel={handleCancel}
          onDryRun={handleDryRun}
          onApply={handleApply}
        />
      </div>
    </div>
  );
}
