"use client";

/**
 * Secrets 화면 — admin.pen Screen 07 / docs/admin-requirements.md §1·§2.2·§4.1.
 *
 * 운영자가 keyring/env/file 백엔드에 등록된 시크릿을 한 화면에서 점검·회전·신규
 * 등록하도록 한다. **값은 어떤 상태에서도 마스킹되거나 마지막 4자리만 5초간** 노출
 * 된다는 단일 보안 원칙을 컴포넌트·이벤트 핸들러·a11y 라이브 리전 모두가 공유한다.
 *
 * 구조:
 *   1. 페이지 헤더 — 새로고침 + 신규 추가
 *   2. SecretsTable — 키 / 출처 / 마지막 회전 / 사용처 배지 / Reveal+Rotate 액션
 *   3. RotateSecretModal — 영향 분석(LLM/Webhook 등) → ConfirmGate → 적용
 *   4. AddSecretModal   — 키 이름 + 백엔드 + (paste-only) 값 입력
 *   5. 라이브 리전 — copy/cut 차단·reveal/rotate 결과를 스크린리더에 안내
 *
 * 보안 결정:
 *   - revealSecret API는 평문을 *반드시 한 번만* 호출자에게 넘긴다. 본 화면은 평문을
 *     state에 *저장하지 않고* 곧장 ``slice(-4)``로 잘라 메모리에서 폐기한다.
 *   - 입력 필드는 ``onCopy``/``onCut``/``onContextMenu``를 모두 차단한다 — 클립보드
 *     자동 복사·우클릭 메뉴 복사를 함께 막아 “값이 떠돌” 경로 자체를 없앤다.
 *   - 차단된 액션은 ``aria-live=polite`` 라이브 리전을 통해 스크린리더에 안내된다.
 */

import { useCallback, useEffect, useId, useMemo, useState } from "react";
import {
  Eye,
  EyeOff,
  KeyRound,
  Plus,
  RefreshCw,
  RotateCw,
  ShieldCheck,
} from "lucide-react";
import { Badge } from "@/components/atoms/Badge";
import { Button } from "@/components/atoms/Button";
import { Input } from "@/components/atoms/Input";
import { SettingCard } from "@/components/molecules/SettingCard";
import { ConfirmGate, Modal, useToast } from "@/components/primitives";
import {
  SECRET_BACKENDS,
  type SecretBackend,
  type SecretMeta,
  type SecretReference,
  areaLabel,
  fetchConfigTree,
  findSecretReferences,
  listSecrets,
  revealSecret,
  rotateSecret,
} from "@/lib/api/secrets";
import { AdminApiError } from "@/lib/api/errors";
import { cn } from "@/lib/cn";

const REVEAL_TTL_MS = 5_000;

/** 화면 전체에서 단일 인스턴스로 유지되는 라이브 리전 메시지. */
type LiveAnnouncement = { id: number; text: string };

export default function SecretsPage() {
  const toast = useToast();
  const [items, setItems] = useState<SecretMeta[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [configTree, setConfigTree] = useState<unknown>(null);

  const [rotateTarget, setRotateTarget] = useState<SecretMeta | null>(null);
  const [showAdd, setShowAdd] = useState(false);

  // Reveal 슬롯은 한 행만 동시에 활성 — 평문 상주 시간을 최소화.
  const [revealedFor, setRevealedFor] = useState<{
    key: string;
    lastFour: string;
  } | null>(null);

  // a11y 라이브 리전 — 복사 차단·reveal·rotate 안내를 스크린리더에 노출.
  const [announcement, setAnnouncement] = useState<LiveAnnouncement>({
    id: 0,
    text: "",
  });
  const announce = useCallback((text: string) => {
    setAnnouncement((prev) => ({ id: prev.id + 1, text }));
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [list, cfg] = await Promise.all([listSecrets(), fetchConfigTree()]);
      setItems(list);
      setConfigTree(cfg);
    } catch (e) {
      const message =
        e instanceof AdminApiError
          ? e.message
          : e instanceof Error
            ? e.message
            : String(e);
      setError(message);
      // 토스트는 fetchAdmin이 자동 emit하므로 여기서 추가 push는 생략.
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Reveal 자동 만료 — 5초 후 강제 마스킹.
  useEffect(() => {
    if (!revealedFor) return;
    const t = window.setTimeout(() => {
      setRevealedFor(null);
      announce("시크릿이 다시 마스킹되었어요.");
    }, REVEAL_TTL_MS);
    return () => window.clearTimeout(t);
  }, [revealedFor, announce]);

  const handleReveal = useCallback(
    async (item: SecretMeta) => {
      const key = `${item.backend}:${item.name}`;
      if (revealedFor?.key === key) {
        setRevealedFor(null);
        return;
      }
      try {
        const res = await revealSecret(item.name, item.backend);
        // 평문은 즉시 폐기 — slice(-4)만 남기고 res.value는 GC에 맡긴다.
        const lastFour = res.value.slice(-4) || "????";
        setRevealedFor({ key, lastFour });
        announce(`${item.name} 시크릿 마지막 4자리를 5초간 표시합니다.`);
      } catch (e) {
        const message =
          e instanceof AdminApiError
            ? e.message
            : e instanceof Error
              ? e.message
              : String(e);
        toast.push({
          tone: "warn",
          title: "Reveal 실패",
          description: message,
        });
      }
    },
    [revealedFor, announce, toast],
  );

  const filteredItems = items ?? [];
  const total = filteredItems.length;

  return (
    <div className="flex flex-col gap-6 pb-24">
      <header className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <KeyRound
            size={28}
            strokeWidth={1.5}
            aria-hidden
            className="mt-1 text-[--primary]"
          />
          <div className="flex flex-col gap-1">
            <h1 className="text-2xl font-semibold text-[--foreground-strong]">
              시크릿
            </h1>
            <p className="text-sm text-[--muted-foreground]">
              keyring · env · file 볼트에 등록된 키를 한 곳에서 점검·회전합니다.
              값은 항상 마스킹되며, Reveal은 마지막 4자리만 5초간 표시한 뒤 자동으로 가립니다.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            leftIcon={<RefreshCw size={14} aria-hidden />}
            onClick={refresh}
            disabled={loading}
          >
            새로고침
          </Button>
          <Button
            variant="primary"
            size="sm"
            leftIcon={<Plus size={14} aria-hidden />}
            onClick={() => setShowAdd(true)}
          >
            신규 추가
          </Button>
        </div>
      </header>

      {error ? (
        <div
          role="alert"
          className="rounded-[--radius-m] border border-[--color-error] bg-[--color-error-bg] p-3 text-sm text-[--color-error]"
        >
          시크릿 메타데이터를 불러오지 못했어요: {error}
        </div>
      ) : null}

      <SettingCard
        title="등록된 시크릿"
        description="회전 액션은 ConfirmGate를 통해 영향 분석을 확인한 뒤 적용됩니다."
        headerRight={
          <Badge tone="info">
            <ShieldCheck size={10} className="mr-1" aria-hidden /> {total}개
          </Badge>
        }
      >
        {loading && !items ? (
          <p className="text-sm text-[--muted-foreground]">불러오는 중…</p>
        ) : filteredItems.length === 0 ? (
          <p className="text-sm text-[--muted-foreground]">
            등록된 시크릿이 없어요. 우상단 “신규 추가”로 keyring/env/file 볼트에 키를 등록하세요.
          </p>
        ) : (
          <SecretsTable
            items={filteredItems}
            configTree={configTree}
            revealedKey={revealedFor?.key ?? null}
            revealedLastFour={revealedFor?.lastFour ?? null}
            onReveal={handleReveal}
            onRotate={(item) => setRotateTarget(item)}
          />
        )}
      </SettingCard>

      {rotateTarget ? (
        <RotateSecretModal
          target={rotateTarget}
          configTree={configTree}
          onClose={() => setRotateTarget(null)}
          onAnnounce={announce}
          onRotated={async (item) => {
            toast.push({
              tone: "success",
              title: `${item.name} 키를 회전했어요.`,
              description: `백엔드: ${item.backend}`,
            });
            announce(`${item.name} 시크릿이 회전되었습니다.`);
            setRotateTarget(null);
            await refresh();
          }}
        />
      ) : null}

      {showAdd ? (
        <AddSecretModal
          existing={filteredItems}
          onClose={() => setShowAdd(false)}
          onAnnounce={announce}
          onAdded={async (item) => {
            toast.push({
              tone: "success",
              title: `${item.name} 키를 등록했어요.`,
              description: `백엔드: ${item.backend}`,
            });
            announce(`${item.name} 시크릿이 등록되었습니다.`);
            setShowAdd(false);
            await refresh();
          }}
        />
      ) : null}

      {/*
        라이브 리전 — copy/cut 차단·reveal/rotate 결과를 모두 한 노드로 모아
        스크린리더가 한 흐름으로 읽도록 한다. ``key``를 바꿔 강제 재낭독.
      */}
      <div
        aria-live="polite"
        aria-atomic="true"
        role="status"
        className="sr-only"
      >
        <span key={announcement.id}>{announcement.text}</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SecretsTable
// ---------------------------------------------------------------------------

interface SecretsTableProps {
  items: SecretMeta[];
  configTree: unknown;
  revealedKey: string | null;
  revealedLastFour: string | null;
  onReveal: (item: SecretMeta) => void;
  onRotate: (item: SecretMeta) => void;
}

function SecretsTable({
  items,
  configTree,
  revealedKey,
  revealedLastFour,
  onReveal,
  onRotate,
}: SecretsTableProps) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-separate border-spacing-y-1 text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wider text-[--muted-foreground]">
            <th className="px-3 py-2 font-medium">키</th>
            <th className="px-3 py-2 font-medium">출처</th>
            <th className="px-3 py-2 font-medium">마지막 회전</th>
            <th className="px-3 py-2 font-medium">사용처</th>
            <th className="px-3 py-2 font-medium" aria-label="값">
              값
            </th>
            <th className="px-3 py-2 font-medium" aria-label="액션">
              {" "}
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const key = `${item.backend}:${item.name}`;
            const refs = findSecretReferences(configTree, item.name);
            const revealed = revealedKey === key;
            return (
              <tr
                key={key}
                className="rounded-[--radius-m] bg-[--surface] align-middle"
              >
                <td className="rounded-l-[--radius-m] px-3 py-3">
                  <code className="font-mono text-sm text-[--foreground-strong]">
                    {item.name}
                  </code>
                </td>
                <td className="px-3 py-3">
                  <BackendBadge backend={item.backend} />
                </td>
                <td className="px-3 py-3">
                  <RotatedAt at={item.last_rotated_at} />
                </td>
                <td className="px-3 py-3">
                  <UsageBadges refs={refs} />
                </td>
                <td className="px-3 py-3">
                  <span
                    aria-label={revealed ? "마지막 4자리" : "마스킹된 시크릿"}
                    className={cn(
                      "inline-flex items-center rounded-[--radius-sm] px-2 py-1 font-mono text-xs",
                      revealed
                        ? "bg-[--color-warning-bg] text-[--color-warning]"
                        : "bg-[--secret-mask-bg] text-[--foreground]",
                    )}
                  >
                    {"••••"}
                    {revealed ? revealedLastFour : "••••"}
                  </span>
                </td>
                <td className="rounded-r-[--radius-m] px-3 py-3">
                  <div className="flex justify-end gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={
                        revealed
                          ? `${item.name} 시크릿 가리기`
                          : `${item.name} 시크릿 마지막 4자리 보기 (5초)`
                      }
                      onClick={() => onReveal(item)}
                    >
                      {revealed ? (
                        <EyeOff size={14} aria-hidden />
                      ) : (
                        <Eye size={14} aria-hidden />
                      )}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={`${item.name} 시크릿 회전`}
                      onClick={() => onRotate(item)}
                    >
                      <RotateCw size={14} aria-hidden />
                    </Button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function BackendBadge({ backend }: { backend: string }) {
  const tone =
    backend === "keyring" ? "brand" : backend === "env" ? "info" : "neutral";
  return <Badge tone={tone}>{backend}</Badge>;
}

function RotatedAt({ at }: { at: string | null }) {
  if (!at) {
    return <span className="text-xs text-[--muted-foreground]">기록 없음</span>;
  }
  // ISO 문자열을 그대로 보여주되 사람이 읽기 쉽게 분 단위까지만.
  const trimmed = at.length > 16 ? at.slice(0, 16).replace("T", " ") : at;
  return (
    <time dateTime={at} className="font-mono text-xs text-[--muted-foreground]">
      {trimmed}
    </time>
  );
}

function UsageBadges({ refs }: { refs: SecretReference[] }) {
  if (refs.length === 0) {
    return <span className="text-xs text-[--muted-foreground]">참조 없음</span>;
  }
  // 영역별로 묶어 한 영역당 한 배지 — 회전 시 영향 범위를 빠르게 파악.
  const grouped = new Map<string, number>();
  for (const r of refs) {
    grouped.set(r.area, (grouped.get(r.area) ?? 0) + 1);
  }
  return (
    <div className="flex flex-wrap gap-1">
      {Array.from(grouped.entries()).map(([area, count]) => (
        <Badge key={area} tone="info">
          {areaLabel(area)}
          {count > 1 ? ` · ${count}` : ""}
        </Badge>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// RotateSecretModal — dry-run 영향 분석 + ConfirmGate
// ---------------------------------------------------------------------------

interface RotateSecretModalProps {
  target: SecretMeta;
  configTree: unknown;
  onClose: () => void;
  onAnnounce: (text: string) => void;
  onRotated: (item: SecretMeta) => void | Promise<void>;
}

function RotateSecretModal({
  target,
  configTree,
  onClose,
  onAnnounce,
  onRotated,
}: RotateSecretModalProps) {
  const [value, setValue] = useState("");
  const [showConfirm, setShowConfirm] = useState(false);
  const [pending, setPending] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const refs = useMemo(
    () => findSecretReferences(configTree, target.name),
    [configTree, target.name],
  );

  async function applyRotate() {
    if (!value) return;
    setPending(true);
    setErrorMsg(null);
    try {
      await rotateSecret(
        target.name,
        value,
        target.backend as SecretBackend,
      );
      // 새 값을 메모리에서 즉시 폐기 — 모달이 닫히면서 state도 사라진다.
      setValue("");
      await onRotated(target);
    } catch (e) {
      const message =
        e instanceof AdminApiError
          ? e.message
          : e instanceof Error
            ? e.message
            : String(e);
      setErrorMsg(message);
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <Modal
        open={!showConfirm}
        onOpenChange={(next) => {
          if (!next) onClose();
        }}
        title={`${target.name} 회전`}
        description={`백엔드: ${target.backend}. 새 값을 입력하면 회전 영향을 미리 확인할 수 있어요.`}
        size="md"
        dismissible={!pending}
        footer={
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={onClose}
              disabled={pending}
            >
              취소
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={!value || pending}
              onClick={() => setShowConfirm(true)}
            >
              영향 확인 후 회전
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <SecretValueInput
            id="rotate-secret-value"
            value={value}
            onChange={setValue}
            label="새 시크릿 값"
            onAnnounce={onAnnounce}
          />
          <ImpactSummary refs={refs} mode="rotate" />
          {errorMsg ? (
            <p
              role="alert"
              className="rounded-[--radius-m] border border-[--color-error] bg-[--color-error-bg] p-2 text-xs text-[--color-error]"
            >
              {errorMsg}
            </p>
          ) : null}
        </div>
      </Modal>

      <ConfirmGate
        open={showConfirm}
        onOpenChange={(next) => {
          if (!next && !pending) setShowConfirm(false);
        }}
        title={`${target.name}을(를) 회전할까요?`}
        description="회전 직후부터 모든 사용처가 새 값으로 인증을 시도합니다. 외부 시스템에서 발급한 키를 미리 활성화해 두세요."
        confirmation={`ROTATE ${target.name}`}
        confirmLabel="Rotate"
        tone="destructive"
        onConfirm={applyRotate}
        isPending={pending}
      >
        <ImpactSummary refs={refs} mode="rotate" compact />
      </ConfirmGate>
    </>
  );
}

// ---------------------------------------------------------------------------
// AddSecretModal — paste-only password input
// ---------------------------------------------------------------------------

interface AddSecretModalProps {
  existing: SecretMeta[];
  onClose: () => void;
  onAnnounce: (text: string) => void;
  onAdded: (item: SecretMeta) => void | Promise<void>;
}

function AddSecretModal({
  existing,
  onClose,
  onAnnounce,
  onAdded,
}: AddSecretModalProps) {
  const [name, setName] = useState("");
  const [backend, setBackend] = useState<SecretBackend>("keyring");
  const [value, setValue] = useState("");
  const [pending, setPending] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const nameInvalid = useMemo(() => {
    if (!name) return false;
    if (!/^[A-Za-z0-9_.-]+$/.test(name)) return true;
    return existing.some((s) => s.name === name && s.backend === backend);
  }, [name, backend, existing]);

  async function submit() {
    if (!name || !value || nameInvalid) return;
    setPending(true);
    setErrorMsg(null);
    try {
      // ``rotate`` 엔드포인트가 신규 등록도 처리한다 — 백엔드 SecretsManager.store가
      // 키 부재 시에는 새 항목을 만들고, 존재하면 덮어쓴다.
      await rotateSecret(name, value, backend);
      setValue("");
      await onAdded({ name, backend, last_rotated_at: null });
    } catch (e) {
      const message =
        e instanceof AdminApiError
          ? e.message
          : e instanceof Error
            ? e.message
            : String(e);
      setErrorMsg(message);
    } finally {
      setPending(false);
    }
  }

  const nameId = useId();
  const backendId = useId();

  return (
    <Modal
      open
      onOpenChange={(next) => {
        if (!next && !pending) onClose();
      }}
      title="시크릿 신규 추가"
      description="키 이름은 영문·숫자·_-. 만 허용됩니다. 값은 붙여넣기만 가능하며 복사·우클릭이 차단돼요."
      size="md"
      dismissible={!pending}
      footer={
        <>
          <Button
            variant="ghost"
            size="sm"
            onClick={onClose}
            disabled={pending}
          >
            취소
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={submit}
            disabled={!name || !value || nameInvalid || pending}
          >
            {pending ? "저장 중…" : "저장"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <label
            htmlFor={nameId}
            className="text-xs font-medium text-[--foreground]"
          >
            키 이름
          </label>
          <Input
            id={nameId}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="예: claude_api_key"
            autoComplete="off"
            invalid={nameInvalid || undefined}
          />
          {nameInvalid ? (
            <p className="text-[11px] text-[--color-error]" role="alert">
              {/^[A-Za-z0-9_.-]+$/.test(name)
                ? `이미 ${backend} 백엔드에 같은 이름이 있어요. 회전을 사용해 주세요.`
                : "영문·숫자·_-. 만 사용할 수 있어요."}
            </p>
          ) : null}
        </div>
        <div className="flex flex-col gap-1">
          <label
            htmlFor={backendId}
            className="text-xs font-medium text-[--foreground]"
          >
            저장 백엔드
          </label>
          <select
            id={backendId}
            value={backend}
            onChange={(e) => setBackend(e.target.value as SecretBackend)}
            className="rounded-[--radius-m] border border-[--border] bg-[--card] px-3 py-2 text-sm text-[--foreground] focus:border-[--primary] focus:outline-none"
          >
            {SECRET_BACKENDS.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </div>
        <SecretValueInput
          id="add-secret-value"
          value={value}
          onChange={setValue}
          label="값"
          onAnnounce={onAnnounce}
        />
        {errorMsg ? (
          <p
            role="alert"
            className="rounded-[--radius-m] border border-[--color-error] bg-[--color-error-bg] p-2 text-xs text-[--color-error]"
          >
            {errorMsg}
          </p>
        ) : null}
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// SecretValueInput — paste-only password input + a11y 안내
// ---------------------------------------------------------------------------

interface SecretValueInputProps {
  id: string;
  label: string;
  value: string;
  onChange: (next: string) => void;
  onAnnounce: (text: string) => void;
}

/**
 * 시크릿 값 입력 필드.
 *
 * 보안 결정:
 *  - ``type="password"``로 화면에서도 가려진다.
 *  - ``onCopy``/``onCut``/``onContextMenu``를 모두 차단해 클립보드 자동 복사·우클릭
 *    복사 메뉴를 함께 제거. ``aria-live`` 라이브 리전에 차단 사실을 안내한다.
 *  - ``autoComplete="off"`` + ``data-form-type="other"``로 비밀번호 매니저의 자동저장
 *    프롬프트도 회피.
 *  - ``onPaste``는 허용 — 운영자는 보통 외부 콘솔에서 새 토큰을 붙여넣는다.
 */
function SecretValueInput({
  id,
  label,
  value,
  onChange,
  onAnnounce,
}: SecretValueInputProps) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-[--foreground]">
        {label}
      </label>
      <input
        id={id}
        type="password"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        // 붙여넣기는 허용, 그 외 클립보드 인터랙션은 모두 차단.
        onCopy={(e) => {
          e.preventDefault();
          onAnnounce("복사가 차단되었어요. 시크릿 값은 붙여넣기만 가능합니다.");
        }}
        onCut={(e) => {
          e.preventDefault();
          onAnnounce("잘라내기가 차단되었어요. 시크릿 값은 붙여넣기만 가능합니다.");
        }}
        onContextMenu={(e) => {
          e.preventDefault();
          onAnnounce("우클릭 메뉴가 차단되었어요.");
        }}
        autoComplete="off"
        autoCapitalize="off"
        autoCorrect="off"
        spellCheck={false}
        data-form-type="other"
        placeholder="외부 콘솔에서 발급한 토큰을 붙여넣으세요"
        className="rounded-[--radius-m] border border-[--border] bg-[--card] px-3 py-2 font-mono text-sm text-[--foreground] outline-none placeholder:text-[--placeholder] focus:border-[--primary]"
      />
      <p className="text-[11px] text-[--muted-foreground]">
        값은 화면에 노출되지 않으며, 복사·우클릭이 차단됩니다. 붙여넣기만 가능합니다.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ImpactSummary — 영향 받는 컴포넌트 목록
// ---------------------------------------------------------------------------

interface ImpactSummaryProps {
  refs: SecretReference[];
  mode: "rotate" | "add";
  compact?: boolean;
}

function ImpactSummary({ refs, mode, compact = false }: ImpactSummaryProps) {
  if (refs.length === 0) {
    return (
      <div className="rounded-[--radius-m] border border-dashed border-[--border-divider] bg-[--surface] p-3 text-xs text-[--muted-foreground]">
        config.yaml에서 이 시크릿을 참조하는 곳이 없어요.{" "}
        {mode === "rotate"
          ? "회전해도 외부 호출에는 즉시 영향이 가지 않습니다."
          : "추가 후 LLM/Webhook/Telegram 등에서 참조 문자열로 연결하세요."}
      </div>
    );
  }
  // dotted path별로 카드 — 회전 후 인증을 다시 하게 될 컴포넌트의 정확한 위치.
  return (
    <div
      className={cn(
        "flex flex-col gap-2 rounded-[--radius-m] border border-[--border-divider] bg-[--surface] p-3",
        compact ? "max-h-32 overflow-y-auto" : undefined,
      )}
    >
      <p className="text-xs font-medium text-[--foreground]">
        영향 받는 컴포넌트 — {refs.length}곳
      </p>
      <ul className="flex flex-col gap-1 text-xs text-[--muted-foreground]">
        {refs.map((r) => (
          <li
            key={r.path}
            className="flex items-center justify-between gap-2 rounded-[--radius-sm] bg-[--card] px-2 py-1"
          >
            <code className="font-mono text-[11px] text-[--foreground]">
              {r.path}
            </code>
            <Badge tone="info">{areaLabel(r.area)}</Badge>
          </li>
        ))}
      </ul>
    </div>
  );
}
