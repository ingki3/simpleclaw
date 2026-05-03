"use client";

/**
 * Channels 화면 (BIZ-51) — admin.pen Screen 08 + admin-requirements.md §1·§5.
 *
 * 두 채널 카드(Telegram / Webhook)를 좌우로 배치한다. 각 카드는 활성 토글,
 * 핵심 설정(토큰·페이로드 한도·rate limit 등), 시크릿 회전, 테스트 발송을 모은다.
 *
 * 적용 정책 라벨 — admin-requirements.md §2.1을 그대로 따른다.
 *  - ``↻ Hot`` : 페이로드 한도/rate limit/whitelist 등 즉시 반영.
 *  - ``♻ Hot-reload`` : 시크릿 변경(토큰)은 다음 호출/재기동 시.
 *  - 봇 토큰·웹훅 enabled는 ``Service-restart``로 봇 재기동이 필요.
 *
 * 페이지는 자체 dirty/dry-run 흐름을 LLM 화면(BIZ-45)과 동일한 패턴으로 가진다 —
 * 각 채널 카드별로 변경 → dry-run → 적용 순. 적용 성공 시 5분 undo 토스트.
 *
 * 테스트 발송은 ``/admin/v1/channels/{name}/test``를 호출하고 토스트로
 * ``status_code/latency_ms``를 표시한다. 비활성 카드는 dim, 카드별 24시간
 * 메시지 카운터(현재 데이터 소스 부재로 placeholder).
 */

import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  MessageSquare,
  RefreshCw,
  Send,
  Webhook,
} from "lucide-react";
import { Badge } from "@/components/atoms/Badge";
import { Button } from "@/components/atoms/Button";
import { Input } from "@/components/atoms/Input";
import { StatusPill, type StatusTone } from "@/components/atoms/StatusPill";
import { Switch } from "@/components/atoms/Switch";
import { SettingCard } from "@/components/molecules/SettingCard";
import { SecretField } from "@/components/molecules/SecretField";
import { useToast } from "@/lib/toast";
import {
  applyTelegramPatch,
  applyWebhookPatch,
  dryRunTelegramPatch,
  dryRunWebhookPatch,
  getChannelsConfig,
  listSecrets,
  parseSecretRef,
  revealSecret,
  rotateSecret,
  testSendChannel,
  undoAudit,
  type ApplyResponse,
  type ChannelsConfig,
  type SecretMeta,
  type TelegramConfig,
  type WebhookConfig,
} from "@/lib/api/channels";

// 5분 undo 윈도 — DESIGN.md §1 #6 Reversibility.
const UNDO_WINDOW_MS = 5 * 60 * 1000;

function isEqual(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

function findSecretMeta(
  secrets: SecretMeta[],
  ref: string | undefined,
): SecretMeta | undefined {
  if (!ref) return undefined;
  const { name } = parseSecretRef(ref);
  return secrets.find((s) => s.name === name);
}

function deriveTelegramHealth(
  cfg: TelegramConfig | undefined,
  meta: SecretMeta | undefined,
): { tone: StatusTone; label: string } {
  if (!cfg) return { tone: "neutral", label: "미설정" };
  if (!cfg.bot_token) return { tone: "warning", label: "토큰 없음" };
  if (!meta) return { tone: "warning", label: "시크릿 미등록" };
  const wl = cfg.whitelist;
  const ids = (wl?.user_ids?.length ?? 0) + (wl?.chat_ids?.length ?? 0);
  if (ids === 0) return { tone: "warning", label: "화이트리스트 비어 있음" };
  return { tone: "success", label: "정상" };
}

function deriveWebhookHealth(
  cfg: WebhookConfig | undefined,
  meta: SecretMeta | undefined,
): { tone: StatusTone; label: string } {
  if (!cfg) return { tone: "neutral", label: "미설정" };
  if (cfg.enabled === false) return { tone: "neutral", label: "비활성" };
  if (!meta && cfg.auth_token) return { tone: "warning", label: "시크릿 미등록" };
  return { tone: "success", label: "정상" };
}

function formatTarget(target: string | undefined): string {
  if (!target) return "—";
  return target.length > 60 ? target.slice(0, 57) + "…" : target;
}

export default function ChannelsPage() {
  const toast = useToast();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [serverConfig, setServerConfig] = useState<ChannelsConfig | null>(null);
  const [tgDraft, setTgDraft] = useState<TelegramConfig | null>(null);
  const [whDraft, setWhDraft] = useState<WebhookConfig | null>(null);
  const [secrets, setSecrets] = useState<SecretMeta[]>([]);

  // 카드별 진행 상태 — 한 카드의 적용 중에 다른 카드 액션을 막지 않는다.
  const [tgBusy, setTgBusy] = useState<"idle" | "dry-run" | "applying" | "test">(
    "idle",
  );
  const [whBusy, setWhBusy] = useState<"idle" | "dry-run" | "applying" | "test">(
    "idle",
  );

  // 마지막 테스트 결과 — 카드 푸터에 status/latency를 표시.
  const [tgLastTest, setTgLastTest] = useState<{
    ok: boolean;
    status: number;
    latencyMs: number;
    at: string;
  } | null>(null);
  const [whLastTest, setWhLastTest] = useState<{
    ok: boolean;
    status: number;
    latencyMs: number;
    at: string;
  } | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [cfg, secs] = await Promise.all([getChannelsConfig(), listSecrets()]);
      const tg: TelegramConfig = {
        whitelist: { user_ids: [], chat_ids: [] },
        ...(cfg.telegram ?? {}),
      };
      const wh: WebhookConfig = {
        enabled: true,
        host: "127.0.0.1",
        port: 8080,
        ...(cfg.webhook ?? {}),
      };
      setServerConfig({ telegram: tg, webhook: wh });
      setTgDraft(tg);
      setWhDraft(wh);
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

  const tgDirty = useMemo(
    () =>
      !!(serverConfig?.telegram && tgDraft && !isEqual(serverConfig.telegram, tgDraft)),
    [serverConfig, tgDraft],
  );
  const whDirty = useMemo(
    () =>
      !!(serverConfig?.webhook && whDraft && !isEqual(serverConfig.webhook, whDraft)),
    [serverConfig, whDraft],
  );

  const tgSecretMeta = useMemo(
    () => findSecretMeta(secrets, tgDraft?.bot_token),
    [secrets, tgDraft?.bot_token],
  );
  const whSecretMeta = useMemo(
    () => findSecretMeta(secrets, whDraft?.auth_token),
    [secrets, whDraft?.auth_token],
  );

  const tgHealth = deriveTelegramHealth(tgDraft ?? undefined, tgSecretMeta);
  const whHealth = deriveWebhookHealth(whDraft ?? undefined, whSecretMeta);

  // diff 패치 — 서버 상태와 비교해 변경된 키만 보낸다.
  function buildPatch<T extends Record<string, unknown>>(
    server: T | undefined,
    draft: T | undefined,
  ): Partial<T> | null {
    if (!server || !draft) return null;
    const patch: Partial<T> = {};
    let changed = false;
    for (const k of Object.keys(draft) as Array<keyof T>) {
      if (!isEqual(draft[k], server[k])) {
        patch[k] = draft[k];
        changed = true;
      }
    }
    return changed ? patch : null;
  }

  function handleApplyToast(
    label: string,
    res: ApplyResponse,
    onUndo?: () => Promise<void>,
  ) {
    toast.push({
      tone: "success",
      title:
        res.outcome === "applied"
          ? `${label} 변경을 적용했습니다.`
          : `${label} 변경이 펜딩되었습니다 — 데몬 재시작 후 적용됩니다.`,
      description: res.policy
        ? `정책: ${res.policy.level} · 영향: ${res.policy.affected_modules.join(", ") || "—"}`
        : undefined,
      onUndo: res.outcome === "applied" ? onUndo : undefined,
    });
  }

  async function handleTelegramApply(dryFirst: boolean) {
    if (!tgDraft) return;
    const patch = buildPatch(serverConfig?.telegram, tgDraft);
    if (!patch) {
      toast.push({ tone: "info", title: "변경 사항이 없습니다." });
      return;
    }
    if (dryFirst) {
      setTgBusy("dry-run");
      try {
        const res = await dryRunTelegramPatch(patch);
        toast.push({
          tone: "info",
          title: "Telegram dry-run 결과",
          description: `정책: ${res.policy.level} · ${res.policy.matched_keys.length} keys`,
        });
      } catch (err) {
        toast.push({
          tone: "error",
          title: "Telegram dry-run 실패",
          description: err instanceof Error ? err.message : String(err),
        });
      } finally {
        setTgBusy("idle");
      }
      return;
    }
    setTgBusy("applying");
    try {
      const res = await applyTelegramPatch(patch);
      handleApplyToast("Telegram", res, async () => {
        try {
          await undoAudit(res.audit_id);
          await load();
          toast.push({ tone: "info", title: "Telegram 변경을 되돌렸습니다." });
        } catch (err) {
          toast.push({
            tone: "error",
            title: "Undo 실패",
            description: err instanceof Error ? err.message : String(err),
          });
        }
      });
      await load();
    } catch (err) {
      toast.push({
        tone: "error",
        title: "Telegram 적용 실패",
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setTgBusy("idle");
    }
  }

  async function handleWebhookApply(dryFirst: boolean) {
    if (!whDraft) return;
    const patch = buildPatch(serverConfig?.webhook, whDraft);
    if (!patch) {
      toast.push({ tone: "info", title: "변경 사항이 없습니다." });
      return;
    }
    if (dryFirst) {
      setWhBusy("dry-run");
      try {
        const res = await dryRunWebhookPatch(patch);
        toast.push({
          tone: "info",
          title: "Webhook dry-run 결과",
          description: `정책: ${res.policy.level} · ${res.policy.matched_keys.length} keys`,
        });
      } catch (err) {
        toast.push({
          tone: "error",
          title: "Webhook dry-run 실패",
          description: err instanceof Error ? err.message : String(err),
        });
      } finally {
        setWhBusy("idle");
      }
      return;
    }
    setWhBusy("applying");
    try {
      const res = await applyWebhookPatch(patch);
      handleApplyToast("Webhook", res, async () => {
        try {
          await undoAudit(res.audit_id);
          await load();
          toast.push({ tone: "info", title: "Webhook 변경을 되돌렸습니다." });
        } catch (err) {
          toast.push({
            tone: "error",
            title: "Undo 실패",
            description: err instanceof Error ? err.message : String(err),
          });
        }
      });
      await load();
    } catch (err) {
      toast.push({
        tone: "error",
        title: "Webhook 적용 실패",
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setWhBusy("idle");
    }
  }

  async function handleTestSend(channel: "telegram" | "webhook") {
    const setBusy = channel === "telegram" ? setTgBusy : setWhBusy;
    const setLast = channel === "telegram" ? setTgLastTest : setWhLastTest;
    setBusy("test");
    try {
      const res = await testSendChannel(channel);
      setLast({
        ok: res.ok,
        status: res.status_code,
        latencyMs: res.latency_ms,
        at: new Date().toISOString(),
      });
      toast.push({
        tone: res.ok ? "success" : "error",
        title: res.ok
          ? `${channel} 테스트 발송 성공 (${res.status_code} · ${res.latency_ms}ms)`
          : `${channel} 테스트 발송 실패 (${res.status_code || "—"} · ${res.latency_ms}ms)`,
        description: res.ok
          ? `target: ${formatTarget(res.target)}`
          : res.error || "응답이 비어 있어요.",
      });
    } catch (err) {
      setLast({
        ok: false,
        status: 0,
        latencyMs: 0,
        at: new Date().toISOString(),
      });
      toast.push({
        tone: "error",
        title: `${channel} 테스트 발송 실패`,
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setBusy("idle");
    }
  }

  return (
    <div className="flex flex-col gap-6 pb-12">
      <header className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <MessageSquare
            size={28}
            strokeWidth={1.5}
            aria-hidden
            className="mt-1 text-(--primary)"
          />
          <div className="flex flex-col gap-1">
            <h1 className="text-2xl font-semibold text-(--foreground-strong)">
              채널
            </h1>
            <p className="text-sm text-(--muted-foreground)">
              Telegram·Webhook 인입 채널의 활성 상태·시크릿·페이로드 한도를 한
              화면에서 관리합니다. 테스트 발송으로 응답 코드와 지연을 즉시
              확인할 수 있어요.
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

      {error ? (
        <div
          role="alert"
          className="rounded-(--radius-m) border border-(--color-error) bg-(--color-error-bg) p-3 text-sm text-(--color-error)"
        >
          채널 설정을 불러오지 못했어요: {error}
        </div>
      ) : null}

      {loading || !tgDraft || !whDraft ? (
        <div className="rounded-(--radius-m) border border-dashed border-(--border) bg-(--surface) p-6 text-sm text-(--muted-foreground)">
          채널 설정을 불러오는 중…
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <TelegramCard
            value={tgDraft}
            health={tgHealth}
            secret={tgSecretMeta}
            dirty={tgDirty}
            busy={tgBusy}
            lastTest={tgLastTest}
            onChange={setTgDraft}
            onCancel={() =>
              setTgDraft(serverConfig?.telegram ?? { whitelist: {} })
            }
            onDryRun={() => handleTelegramApply(true)}
            onApply={() => handleTelegramApply(false)}
            onTestSend={() => handleTestSend("telegram")}
            onSecretRotated={load}
          />
          <WebhookCard
            value={whDraft}
            health={whHealth}
            secret={whSecretMeta}
            dirty={whDirty}
            busy={whBusy}
            lastTest={whLastTest}
            onChange={setWhDraft}
            onCancel={() => setWhDraft(serverConfig?.webhook ?? {})}
            onDryRun={() => handleWebhookApply(true)}
            onApply={() => handleWebhookApply(false)}
            onTestSend={() => handleTestSend("webhook")}
            onSecretRotated={load}
          />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Telegram 카드
// ---------------------------------------------------------------------------

interface ChannelCardProps<T> {
  value: T;
  health: { tone: StatusTone; label: string };
  secret: SecretMeta | undefined;
  dirty: boolean;
  busy: "idle" | "dry-run" | "applying" | "test";
  lastTest: {
    ok: boolean;
    status: number;
    latencyMs: number;
    at: string;
  } | null;
  onChange: (next: T) => void;
  onCancel: () => void;
  onDryRun: () => void;
  onApply: () => void;
  onTestSend: () => void;
  onSecretRotated: () => void;
}

function TelegramCard({
  value,
  health,
  secret,
  dirty,
  busy,
  lastTest,
  onChange,
  onCancel,
  onDryRun,
  onApply,
  onTestSend,
  onSecretRotated,
}: ChannelCardProps<TelegramConfig>) {
  const enabled = !!value.bot_token; // 토큰 존재 여부로 활성 상태 추정.
  const dim = !enabled;

  return (
    <SettingCard
      title="Telegram"
      description="단일 운영자 봇 — 봇 토큰 + user/chat 화이트리스트로 메시지를 받습니다."
      headerRight={
        <>
          <Badge tone="info">♻ Hot-reload</Badge>
          <StatusPill tone={health.tone}>{health.label}</StatusPill>
        </>
      }
      className={dim ? "opacity-70" : undefined}
    >
      <div className="flex flex-col gap-4">
        <SecretBlock
          label="봇 토큰"
          ref={value.bot_token}
          secret={secret}
          onSecretRotated={onSecretRotated}
          policyLabel="Service-restart"
          policyHint="봇 토큰 변경은 봇 재기동을 트리거합니다."
        />

        <WhitelistEditor
          value={value.whitelist ?? {}}
          onChange={(next) => onChange({ ...value, whitelist: next })}
        />

        <MessageCounter channel="telegram" />

        <CardFooter
          channel="telegram"
          dirty={dirty}
          busy={busy}
          lastTest={lastTest}
          onCancel={onCancel}
          onDryRun={onDryRun}
          onApply={onApply}
          onTestSend={onTestSend}
        />
      </div>
    </SettingCard>
  );
}

// ---------------------------------------------------------------------------
// Webhook 카드
// ---------------------------------------------------------------------------

function WebhookCard({
  value,
  health,
  secret,
  dirty,
  busy,
  lastTest,
  onChange,
  onCancel,
  onDryRun,
  onApply,
  onTestSend,
  onSecretRotated,
}: ChannelCardProps<WebhookConfig>) {
  const enabled = value.enabled !== false;
  const dim = !enabled;

  function patch(next: Partial<WebhookConfig>) {
    onChange({ ...value, ...next });
  }

  // ``http://{host}:{port}/webhook`` — 운영자가 외부에 노출하는 인입 URL.
  const url = `http://${value.host ?? "127.0.0.1"}:${value.port ?? 8080}/webhook`;

  return (
    <SettingCard
      title="Webhook"
      description="HTTP 인입 채널 — 페이로드 한도, rate limit, 동시성 cap을 제어합니다."
      headerRight={
        <>
          <Badge tone="success">↻ Hot</Badge>
          <StatusPill tone={health.tone}>{health.label}</StatusPill>
        </>
      }
      className={dim ? "opacity-70" : undefined}
    >
      <div className="flex flex-col gap-4">
        <header className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Webhook size={16} className="text-(--muted-foreground)" aria-hidden />
            <code className="font-mono text-xs text-(--muted-foreground)">
              {url}
            </code>
          </div>
          <Switch
            checked={enabled}
            onCheckedChange={(next) => patch({ enabled: next })}
            label="Webhook 활성"
          />
        </header>

        <SecretBlock
          label="인증 토큰"
          ref={value.auth_token}
          secret={secret}
          onSecretRotated={onSecretRotated}
          policyLabel="Hot"
          policyHint="auth_token 회전은 즉시 반영됩니다."
        />

        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <NumberField
            label="페이로드 한도 (bytes)"
            value={value.max_body_size}
            min={1}
            max={16 * 1024 * 1024}
            placeholder="1048576"
            onChange={(n) => patch({ max_body_size: n })}
            badge={<Badge tone="success">↻ Hot</Badge>}
          />
          <NumberField
            label="Rate limit (요청/윈도)"
            value={value.rate_limit}
            min={0}
            max={100000}
            placeholder="60"
            onChange={(n) => patch({ rate_limit: n })}
            badge={<Badge tone="success">↻ Hot</Badge>}
          />
          <NumberField
            label="Rate limit window (초)"
            value={value.rate_limit_window}
            min={0.001}
            max={86400}
            step={0.1}
            placeholder="60"
            onChange={(n) => patch({ rate_limit_window: n })}
          />
          <NumberField
            label="동시 연결 한도"
            value={value.max_concurrent_connections}
            min={1}
            max={1024}
            placeholder="32"
            onChange={(n) => patch({ max_concurrent_connections: n })}
          />
          <NumberField
            label="대기 큐 크기"
            value={value.queue_size}
            min={0}
            max={8192}
            placeholder="64"
            onChange={(n) => patch({ queue_size: n })}
          />
          <NumberField
            label="알림 쿨다운 (초)"
            value={value.alert_cooldown}
            min={0}
            max={86400}
            step={1}
            placeholder="300"
            onChange={(n) => patch({ alert_cooldown: n })}
          />
        </div>

        <MessageCounter channel="webhook" />

        <CardFooter
          channel="webhook"
          dirty={dirty}
          busy={busy}
          lastTest={lastTest}
          onCancel={onCancel}
          onDryRun={onDryRun}
          onApply={onApply}
          onTestSend={onTestSend}
        />
      </div>
    </SettingCard>
  );
}

// ---------------------------------------------------------------------------
// 보조 컴포넌트
// ---------------------------------------------------------------------------

function SecretBlock({
  label,
  ref: secretRef,
  secret,
  onSecretRotated,
  policyLabel,
  policyHint,
}: {
  label: string;
  ref: string | undefined;
  secret: SecretMeta | undefined;
  onSecretRotated: () => void;
  policyLabel: string;
  policyHint: string;
}) {
  const toast = useToast();
  const [rotating, setRotating] = useState(false);
  const [newValue, setNewValue] = useState("");
  const [showRotate, setShowRotate] = useState(false);
  const { backend, name } = parseSecretRef(secretRef);

  async function handleReveal(): Promise<string | undefined> {
    if (!name) {
      toast.push({
        tone: "info",
        title: "Reveal 불가",
        description: "시크릿 참조 형식이 아닙니다 (예: keyring:telegram_bot_token).",
      });
      return undefined;
    }
    try {
      const res = await revealSecret(name, backend);
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
    if (!name) {
      toast.push({
        tone: "info",
        title: "Rotate 불가",
        description: "시크릿 참조 형식이 아닙니다.",
      });
      return;
    }
    if (!newValue) return;
    setRotating(true);
    try {
      await rotateSecret(name, newValue, backend);
      toast.push({
        tone: "success",
        title: `${label}을 회전했습니다`,
        description: `백엔드: ${backend ?? "auto"}.`,
      });
      setNewValue("");
      setShowRotate(false);
      onSecretRotated();
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
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-(--muted-foreground)">
          {label}
        </span>
        <span
          className="text-[10px] text-(--muted-foreground)"
          title={policyHint}
        >
          {policyLabel}
        </span>
      </div>
      <SecretField
        name={secretRef || `${label} (미설정)`}
        lastFour={name ? name.slice(-4) : "????"}
        onReveal={handleReveal}
        onRotate={() => setShowRotate((p) => !p)}
        revealTtlMs={5_000}
      />
      {secret?.last_rotated_at ? (
        <span className="text-[10px] text-(--muted-foreground)">
          마지막 회전: {new Date(secret.last_rotated_at).toLocaleString("ko-KR")}
        </span>
      ) : null}
      {showRotate ? (
        <div className="flex flex-col gap-2 rounded-(--radius-m) border border-dashed border-(--border-strong) bg-(--surface) p-3">
          <Input
            type="password"
            autoComplete="off"
            placeholder={`새 ${label}`}
            value={newValue}
            onChange={(e) => setNewValue(e.target.value)}
          />
          <div className="flex justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setShowRotate(false);
                setNewValue("");
              }}
            >
              취소
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={!newValue || rotating}
              onClick={handleRotate}
            >
              {rotating ? "회전 중…" : "Rotate"}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function WhitelistEditor({
  value,
  onChange,
}: {
  value: TelegramConfig["whitelist"];
  onChange: (next: TelegramConfig["whitelist"]) => void;
}) {
  const userIds = (value?.user_ids ?? []) as number[];
  const chatIds = (value?.chat_ids ?? []) as number[];

  function setUserIds(raw: string) {
    const parsed = parseIdList(raw);
    onChange({ ...(value ?? {}), user_ids: parsed });
  }
  function setChatIds(raw: string) {
    const parsed = parseIdList(raw);
    onChange({ ...(value ?? {}), chat_ids: parsed });
  }

  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
      <div className="flex flex-col gap-1">
        <label className="text-xs font-medium text-(--muted-foreground)">
          허용 user_ids (쉼표 구분)
        </label>
        <Input
          inputMode="numeric"
          placeholder="123456789, 987654321"
          defaultValue={userIds.join(", ")}
          onBlur={(e) => setUserIds(e.target.value)}
        />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-xs font-medium text-(--muted-foreground)">
          허용 chat_ids (쉼표 구분)
        </label>
        <Input
          inputMode="numeric"
          placeholder="-1001234567890"
          defaultValue={chatIds.join(", ")}
          onBlur={(e) => setChatIds(e.target.value)}
        />
      </div>
    </div>
  );
}

function parseIdList(raw: string): number[] {
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => Number(s))
    .filter((n) => Number.isFinite(n) && Number.isInteger(n));
}

function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  step,
  placeholder,
  badge,
}: {
  label: string;
  value: number | undefined;
  onChange: (next: number | undefined) => void;
  min?: number;
  max?: number;
  step?: number;
  placeholder?: string;
  badge?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between gap-2">
        <label className="text-xs font-medium text-(--muted-foreground)">
          {label}
        </label>
        {badge}
      </div>
      <Input
        type="number"
        inputMode="numeric"
        min={min}
        max={max}
        step={step}
        placeholder={placeholder}
        value={value ?? ""}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === "") {
            onChange(undefined);
            return;
          }
          const n = Number(raw);
          if (Number.isFinite(n)) onChange(n);
        }}
      />
    </div>
  );
}

function MessageCounter({ channel: _channel }: { channel: "telegram" | "webhook" }) {
  // 24h 메시지 카운터 — 데이터 소스가 아직 노출되지 않아 placeholder.
  // BIZ-25 trace 로그/WebhookMetrics 노출 후 본 컴포넌트를 SWR로 교체한다.
  return (
    <div className="flex items-center gap-3 rounded-(--radius-m) border border-dashed border-(--border) bg-(--surface) px-3 py-2 text-xs text-(--muted-foreground)">
      <span className="font-medium text-(--foreground)">최근 24h 메시지</span>
      <span className="font-mono">— 집계 대기</span>
    </div>
  );
}

function CardFooter({
  channel,
  dirty,
  busy,
  lastTest,
  onCancel,
  onDryRun,
  onApply,
  onTestSend,
}: {
  channel: "telegram" | "webhook";
  dirty: boolean;
  busy: "idle" | "dry-run" | "applying" | "test";
  lastTest: {
    ok: boolean;
    status: number;
    latencyMs: number;
    at: string;
  } | null;
  onCancel: () => void;
  onDryRun: () => void;
  onApply: () => void;
  onTestSend: () => void;
}) {
  const applying = busy === "applying";
  const dryRunning = busy === "dry-run";
  const testing = busy === "test";

  return (
    <footer className="mt-2 flex flex-wrap items-center justify-between gap-3 border-t border-(--border) pt-3">
      <div className="flex items-center gap-2 text-xs">
        {lastTest ? (
          <span
            className={
              lastTest.ok
                ? "inline-flex items-center gap-1 text-(--color-success)"
                : "inline-flex items-center gap-1 text-(--color-error)"
            }
          >
            {lastTest.ok ? <CheckCircle2 size={12} aria-hidden /> : null}
            <span className="font-mono">
              {lastTest.ok ? "OK" : "FAIL"} · {lastTest.status || "—"} ·{" "}
              {lastTest.latencyMs}ms
            </span>
          </span>
        ) : (
          <span className="text-(--muted-foreground)">테스트 발송 미수행</span>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="ghost"
          size="sm"
          leftIcon={<Send size={14} aria-hidden />}
          onClick={onTestSend}
          disabled={testing}
        >
          {testing ? "발송 중…" : "테스트 발송"}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={onCancel}
          disabled={!dirty || applying}
        >
          취소
        </Button>
        <Button
          variant="secondary"
          size="sm"
          onClick={onDryRun}
          disabled={!dirty || dryRunning || applying}
        >
          {dryRunning ? "Dry-run 중…" : "Dry-run"}
        </Button>
        <Button
          variant="primary"
          size="sm"
          onClick={onApply}
          disabled={!dirty || applying}
          aria-label={`${channel} 변경 적용`}
        >
          {applying ? "적용 중…" : "적용"}
        </Button>
      </div>
    </footer>
  );
}
