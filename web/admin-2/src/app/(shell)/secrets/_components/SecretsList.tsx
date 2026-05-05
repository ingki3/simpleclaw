"use client";

/**
 * SecretsList — admin.pen `M99Mh` (Secrets Shell) 의 본문 표 박제.
 *
 * MaskedSecretRow (molecule) 를 한 줄씩 나열하고, 각 행에 PolicyChip + 마지막
 * 회전/사용 시각을 메타로 노출. reveal/copy/rotate 액션은 부모(page) 가 모달
 * 토글 + 콘솔 박제로 처리하도록 콜백 prop 으로 위임 — 본 컴포넌트는 시각만
 * 책임진다 (DESIGN.md §4.2 + §1 Principle 2).
 *
 * DESIGN.md §1 Principle 3 — default / loading / empty / error 4-variant 박제.
 * variant 검증은 page.tsx 의 `?secrets=loading|empty|error` 쿼리로 강제.
 */

import { Badge } from "@/design/atoms/Badge";
import { Button } from "@/design/atoms/Button";
import { StatusPill } from "@/design/atoms/StatusPill";
import { EmptyState } from "@/design/molecules/EmptyState";
import { MaskedSecretRow } from "@/design/molecules/MaskedSecretRow";
import { PolicyChip } from "@/design/molecules/PolicyChip";
import { cn } from "@/lib/cn";
import {
  SCOPE_LABEL,
  type SecretRecord,
  type SecretScope,
} from "../_data";

export type SecretsListState = "default" | "empty" | "loading" | "error";

interface SecretsListProps {
  state: SecretsListState;
  secrets?: readonly SecretRecord[];
  /** 그룹 헤더 노출 여부 — 검색 활성 시에는 노이즈 줄이려고 false. */
  grouped?: boolean;
  /** reveal — 부모가 keyring fetch 를 박제 (본 단계는 console.info 만). */
  onReveal?: (id: string) => void;
  /** copy — 부모가 navigator.clipboard 호출, 토스트 박제. */
  onCopy?: (id: string) => void;
  /** rotate — 부모가 Rotate ConfirmGate 모달을 띄움. */
  onRotate?: (secret: SecretRecord) => void;
  /** error 시 노출할 사람 친화 메시지. */
  errorMessage?: string;
  onRetry?: () => void;
  /** 검색어 — 빈 결과 안내문 분기. */
  searchQuery?: string;
  /** Empty 의 CTA — 보통 페이지 헤더의 "시크릿 추가" 와 동일 동작. */
  onAdd?: () => void;
  className?: string;
}

const SKELETON_COUNT = 5;

export function SecretsList({
  state,
  secrets = [],
  grouped = true,
  onReveal,
  onCopy,
  onRotate,
  errorMessage = "시크릿 목록을 불러오지 못했습니다.",
  onRetry,
  searchQuery,
  onAdd,
  className,
}: SecretsListProps) {
  const isFiltered = Boolean(searchQuery && searchQuery.trim().length > 0);
  return (
    <section
      data-testid="secrets-list"
      data-state={state}
      aria-label="시크릿 목록"
      aria-busy={state === "loading" || undefined}
      className={cn("flex flex-col gap-4", className)}
    >
      {state === "loading" ? <ListLoading /> : null}
      {state === "error" ? (
        <ListError message={errorMessage} onRetry={onRetry} />
      ) : null}
      {state === "empty" ? (
        <ListEmpty filtered={false} onAdd={onAdd} />
      ) : null}
      {state === "default" ? (
        secrets.length === 0 ? (
          <ListEmpty filtered={isFiltered} onAdd={onAdd} />
        ) : grouped && !isFiltered ? (
          <GroupedSecrets
            secrets={secrets}
            onReveal={onReveal}
            onCopy={onCopy}
            onRotate={onRotate}
          />
        ) : (
          <FlatSecrets
            secrets={secrets}
            onReveal={onReveal}
            onCopy={onCopy}
            onRotate={onRotate}
          />
        )
      ) : null}
    </section>
  );
}

interface RowsProps {
  secrets: readonly SecretRecord[];
  onReveal?: (id: string) => void;
  onCopy?: (id: string) => void;
  onRotate?: (secret: SecretRecord) => void;
}

function GroupedSecrets({ secrets, onReveal, onCopy, onRotate }: RowsProps) {
  // scope 별로 묶는다 — 같은 scope 가 인접해 있도록 fixture 순서를 따른다.
  const groups = new Map<SecretScope, SecretRecord[]>();
  for (const s of secrets) {
    const list = groups.get(s.scope) ?? [];
    list.push(s);
    groups.set(s.scope, list);
  }
  return (
    <div data-testid="secrets-grouped" className="flex flex-col gap-5">
      {Array.from(groups.entries()).map(([scope, list]) => (
        <div
          key={scope}
          data-testid={`secrets-group-${scope}`}
          className="flex flex-col gap-1"
        >
          <header className="flex items-center justify-between gap-2 px-1">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-(--muted-foreground)">
              {SCOPE_LABEL[scope]}
            </h3>
            <Badge tone="neutral" size="sm">
              {list.length}
            </Badge>
          </header>
          <ul className="flex flex-col rounded-(--radius-l) border border-(--border) bg-(--card) px-3">
            {list.map((s) => (
              <SecretListItem
                key={s.id}
                secret={s}
                onReveal={onReveal}
                onCopy={onCopy}
                onRotate={onRotate}
              />
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function FlatSecrets({ secrets, onReveal, onCopy, onRotate }: RowsProps) {
  return (
    <ul
      data-testid="secrets-flat"
      className="flex flex-col rounded-(--radius-l) border border-(--border) bg-(--card) px-3"
    >
      {secrets.map((s) => (
        <SecretListItem
          key={s.id}
          secret={s}
          onReveal={onReveal}
          onCopy={onCopy}
          onRotate={onRotate}
        />
      ))}
    </ul>
  );
}

function SecretListItem({
  secret,
  onReveal,
  onCopy,
  onRotate,
}: {
  secret: SecretRecord;
  onReveal?: (id: string) => void;
  onCopy?: (id: string) => void;
  onRotate?: (secret: SecretRecord) => void;
}) {
  return (
    <li
      data-testid={`secret-row-${secret.id}`}
      className="flex flex-col gap-1 border-b border-(--border) py-2 last:border-b-0"
    >
      <MaskedSecretRow
        keyName={secret.keyName}
        // 평문은 절대 prop 으로 전달하지 않는다 — 부모가 reveal 콜백에서 console
        // 박제만 하고, 실제 평문 fetch 는 후속 sub-issue 가 책임.
        maskedPreview={secret.maskedPreview}
        onReveal={onReveal ? () => onReveal(secret.id) : undefined}
        onCopy={onCopy ? () => onCopy(secret.id) : undefined}
        onRotate={onRotate ? () => onRotate(secret) : undefined}
        meta={
          <span data-testid={`secret-row-${secret.id}-meta`}>
            {formatMeta(secret)}
          </span>
        }
      />
      <div className="flex flex-wrap items-center gap-2 pl-1 text-[11px] text-(--muted-foreground)">
        <PolicyChip kind={secret.policy} />
        {secret.note ? (
          <span className="break-words">— {secret.note}</span>
        ) : null}
      </div>
    </li>
  );
}

/** 상대 시각 표시 — "12일 전", "방금 전" 등. updatedAt/createdAt 없는 경우 "—". */
export function formatRelative(iso: string | null): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  const diff = Date.now() - t;
  if (diff < 60_000) return "방금 전";
  const min = Math.floor(diff / 60_000);
  if (min < 60) return `${min}분 전`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}시간 전`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day}일 전`;
  const mo = Math.floor(day / 30);
  return `${mo}개월 전`;
}

function formatMeta(secret: SecretRecord): string {
  const rotated = secret.lastRotatedAt
    ? `회전 ${formatRelative(secret.lastRotatedAt)}`
    : "회전 이력 없음";
  const used = secret.lastUsedAt
    ? `사용 ${formatRelative(secret.lastUsedAt)}`
    : "사용 이력 없음";
  return `${rotated} · ${used}`;
}

function ListLoading() {
  return (
    <div
      role="status"
      aria-label="시크릿 목록 로딩 중"
      data-testid="secrets-list-loading"
      className="flex flex-col gap-2 rounded-(--radius-l) border border-(--border) bg-(--card) p-3"
    >
      {Array.from({ length: SKELETON_COUNT }).map((_, i) => (
        <div
          key={i}
          className="flex animate-pulse items-center gap-3 border-b border-(--border) py-2 last:border-b-0"
        >
          <span className="h-4 w-40 rounded-(--radius-sm) bg-(--surface)" />
          <span className="h-7 w-48 rounded-(--radius-m) bg-(--surface)" />
          <span className="ml-auto h-3 w-32 rounded-(--radius-sm) bg-(--surface)" />
        </div>
      ))}
    </div>
  );
}

function ListEmpty({
  filtered,
  onAdd,
}: {
  filtered: boolean;
  onAdd?: () => void;
}) {
  if (filtered) {
    return (
      <div data-testid="secrets-list-empty" data-empty-reason="filtered">
        <EmptyState
          title="검색 결과가 없어요"
          description="다른 키워드로 다시 시도하거나, 검색을 비워 전체 시크릿을 살펴보세요."
        />
      </div>
    );
  }
  return (
    <div data-testid="secrets-list-empty" data-empty-reason="none">
      <EmptyState
        title="저장된 시크릿이 없어요"
        description="API 키, 봇 토큰, 서명 키를 추가하면 라우터·채널·데몬이 즉시 사용합니다."
        action={
          onAdd ? (
            <Button
              variant="primary"
              onClick={onAdd}
              data-testid="secrets-list-empty-cta"
            >
              ＋ 시크릿 추가
            </Button>
          ) : null
        }
      />
    </div>
  );
}

function ListError({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      data-testid="secrets-list-error"
      className="flex flex-col items-start gap-2 rounded-(--radius-m) border border-(--color-error) bg-(--color-error-bg) p-4 text-sm"
    >
      <div className="flex items-center gap-2">
        <StatusPill tone="error">실패</StatusPill>
        <Badge tone="danger" size="sm">
          keyring
        </Badge>
        <span className="font-medium text-(--color-error)">{message}</span>
      </div>
      <p className="text-xs text-(--muted-foreground)">
        keyring 데몬이 일시적으로 잠겼거나 권한이 거부되었을 수 있어요.
        잠시 후 자동 재시도되지만, 즉시 다시 시도하려면 아래 버튼을 누르세요.
      </p>
      {onRetry ? (
        <Button
          size="sm"
          variant="secondary"
          onClick={onRetry}
          data-testid="secrets-list-retry"
        >
          다시 불러오기
        </Button>
      ) : null}
    </div>
  );
}
