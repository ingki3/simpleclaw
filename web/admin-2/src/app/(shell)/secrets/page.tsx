/**
 * /secrets — Admin 2.0 S9 (BIZ-120).
 *
 * admin.pen `M99Mh` (Secrets Shell) 의 콘텐츠 영역을 React 로 박제한다.
 * 구성 (위 → 아래):
 *  1) 페이지 헤더 — h1 "시크릿" + 한 줄 설명 + 검색 입력 + "＋ 시크릿 추가" 버튼.
 *  2) SecretsList — MaskedSecretRow 기반 목록, scope 별 그룹.
 *     `?secrets=loading|empty|error` 쿼리로 4-variant 검증.
 *  3) AddSecretModal (`Nm9nU`) — 키 이름 + 값 + 정책 + 메모.
 *  4) RotateConfirmModal — ConfirmGate (BIZ-109 P1) + 카운트다운.
 *
 * 보안 경계:
 *  - 본 단계는 정적 fixture 만 사용하고, 실제 keyring API 는 미연결.
 *  - reveal/copy/rotate 콜백은 *시크릿 값* 이 아닌 *키 ID* 만 console 로 박제한다.
 *  - Add 시에도 평문은 maskedPreview 사전계산 후 즉시 폐기 — fixture state 에는
 *    `••••<last4>` 만 저장한다.
 */
"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useMemo, useState } from "react";
import { findAreaByPath } from "@/app/areas";
import { Badge } from "@/design/atoms/Badge";
import { Button } from "@/design/atoms/Button";
import { Input } from "@/design/atoms/Input";
import {
  AddSecretModal,
  maskValue,
  type AddSecretInput,
} from "./_components/AddSecretModal";
import { RotateConfirmModal } from "./_components/RotateConfirmModal";
import {
  SecretsList,
  type SecretsListState,
} from "./_components/SecretsList";
import { getSecretsSnapshot, type SecretRecord } from "./_data";

const VALID_LIST_STATES: readonly SecretsListState[] = [
  "default",
  "empty",
  "loading",
  "error",
];

export default function SecretsPage() {
  return (
    <Suspense fallback={null}>
      <SecretsContent />
    </Suspense>
  );
}

function SecretsContent() {
  const area = findAreaByPath("/secrets");
  const snapshot = useMemo(() => getSecretsSnapshot(), []);

  // 4-variant 쿼리 — 다른 영역(memory/skills-recipes/cron) 과 동일 패턴.
  const params = useSearchParams();
  const listState = readState(params.get("secrets"));

  // 로컬 mutable 상태 — Add/Rotate 가 즉시 반영되도록.
  const [secrets, setSecrets] = useState<SecretRecord[]>(() => [
    ...snapshot.secrets,
  ]);
  const [search, setSearch] = useState("");
  const [addOpen, setAddOpen] = useState(false);
  const [rotateTarget, setRotateTarget] = useState<SecretRecord | null>(null);

  const filtered = useMemo(
    () => applyFilter(secrets, search),
    [secrets, search],
  );

  const policyCounts = useMemo(() => summarizePolicies(secrets), [secrets]);

  const existingKeyNames = useMemo(
    () => secrets.map((s) => s.keyName),
    [secrets],
  );

  // 보안 — 콘솔 박제는 *키 ID* 만, 평문은 절대 흘리지 않는다.
  const handleReveal = useCallback((id: string) => {
    if (typeof console !== "undefined") {
      console.info("[secrets] reveal request", id);
    }
  }, []);

  const handleCopy = useCallback((id: string) => {
    if (typeof console !== "undefined") {
      console.info("[secrets] copy request", id);
    }
  }, []);

  const handleRotate = useCallback((secret: SecretRecord) => {
    setRotateTarget(secret);
  }, []);

  const handleAddSubmit = useCallback((input: AddSecretInput) => {
    // 평문은 maskedPreview 만 사전계산한 직후 폐기 — fixture state 에는
    // 마스킹된 값과 메타만 저장한다 (DoD: 시크릿 값이 콘솔/네트워크에 노출 X).
    const masked = maskValue(input.value);
    const valueLength = input.value.length;
    setSecrets((cur) => [
      {
        id: `keyring:${input.keyName}`,
        keyName: input.keyName,
        scope: input.scope,
        maskedPreview: masked,
        policy: input.policy,
        lastRotatedAt: null,
        lastUsedAt: null,
        note: input.note || undefined,
      },
      ...cur,
    ]);
    if (typeof console !== "undefined") {
      console.info(
        "[secrets] add",
        input.keyName,
        input.scope,
        input.policy,
        // 평문 길이만 박제 — 평문 자체는 어디에도 흘리지 않는다.
        `len=${valueLength}`,
      );
    }
  }, []);

  const handleRotateConfirm = useCallback((secret: SecretRecord) => {
    // 회전 박제 — 마지막 회전 시각만 갱신, 마스킹 미리보기는 새 4자리로 swap.
    // 실제 keyring rotate 호출은 후속 sub-issue 가 책임.
    const newPreview = `••••${randomLast4()}`;
    setSecrets((cur) =>
      cur.map((s) =>
        s.id === secret.id
          ? {
              ...s,
              maskedPreview: newPreview,
              lastRotatedAt: new Date().toISOString(),
            }
          : s,
      ),
    );
    if (typeof console !== "undefined") {
      console.info("[secrets] rotate", secret.id);
    }
    setRotateTarget(null);
  }, []);

  return (
    <section
      className="mx-auto flex max-w-6xl flex-col gap-6 p-8"
      data-testid="secrets-page"
    >
      <header className="flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <div className="flex flex-col gap-1">
            <h1 className="text-2xl font-semibold text-(--foreground-strong)">
              {area?.label ?? "시크릿"}
            </h1>
            <p className="text-sm text-(--muted-foreground)">
              keyring 의 API 키·봇 토큰·서명 키를 관리합니다 (DESIGN.md §4.2).
            </p>
          </div>
          <div
            data-testid="secrets-counts"
            className="flex items-center gap-2 text-xs text-(--muted-foreground)"
          >
            <Badge tone="neutral">전체 {secrets.length}</Badge>
            <Badge tone="success">Hot {policyCounts.hot}</Badge>
            <Badge tone="warning">
              Service {policyCounts.serviceRestart}
            </Badge>
            <Badge tone="danger">
              Process {policyCounts.processRestart}
            </Badge>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="min-w-[260px] flex-1">
            <Input
              value={search}
              onChange={(e) => setSearch(e.currentTarget.value)}
              placeholder="키 이름·메모로 검색"
              leading={<span aria-hidden>⌕</span>}
              data-testid="secrets-search"
            />
          </div>
          <Button
            variant="primary"
            onClick={() => setAddOpen(true)}
            data-testid="secrets-add"
          >
            ＋ 시크릿 추가
          </Button>
        </div>
      </header>

      <SecretsList
        state={listState}
        secrets={filtered}
        searchQuery={search}
        onReveal={handleReveal}
        onCopy={handleCopy}
        onRotate={handleRotate}
        onAdd={() => setAddOpen(true)}
        onRetry={() => {
          if (typeof console !== "undefined") {
            console.info("[secrets] retry list fetch");
          }
        }}
      />

      <AddSecretModal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        scopes={snapshot.scopes}
        existingKeyNames={existingKeyNames}
        onSubmit={handleAddSubmit}
      />

      <RotateConfirmModal
        target={rotateTarget}
        onClose={() => setRotateTarget(null)}
        onConfirm={handleRotateConfirm}
      />
    </section>
  );
}

/** ?secrets=foo 의 4-variant 매핑 — 알 수 없으면 default. */
function readState(raw: string | null): SecretsListState {
  if (raw && (VALID_LIST_STATES as readonly string[]).includes(raw)) {
    return raw as SecretsListState;
  }
  return "default";
}

interface Searchable {
  keyName: string;
  note?: string;
}

/** 검색 — keyName/note substring (case-insensitive). */
function applyFilter<T extends Searchable>(
  items: readonly T[],
  query: string,
): T[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...items];
  return items.filter((item) => {
    if (item.keyName.toLowerCase().includes(q)) return true;
    if (item.note && item.note.toLowerCase().includes(q)) return true;
    return false;
  });
}

interface PolicyCounts {
  hot: number;
  serviceRestart: number;
  processRestart: number;
}

function summarizePolicies(items: readonly SecretRecord[]): PolicyCounts {
  let hot = 0;
  let serviceRestart = 0;
  let processRestart = 0;
  for (const s of items) {
    if (s.policy === "hot") hot += 1;
    else if (s.policy === "service-restart") serviceRestart += 1;
    else if (s.policy === "process-restart") processRestart += 1;
  }
  return { hot, serviceRestart, processRestart };
}

/** Rotate 박제용 4자리 — 평문 미사용. */
function randomLast4(): string {
  return Math.floor(Math.random() * 10000)
    .toString()
    .padStart(4, "0");
}
