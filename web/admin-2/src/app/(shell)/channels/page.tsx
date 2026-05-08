/**
 * /channels — Admin 2.0 S10 (BIZ-121).
 *
 * admin.pen `weuuW` (Channels Shell) 의 콘텐츠 영역을 React 로 박제한다.
 * 구성 (위 → 아래):
 *  1) 페이지 헤더 — h1 "채널" + 한 줄 설명.
 *  2) TelegramCard — Bot Token 마스킹 + StatusPill + Allowlist + 회전/저장.
 *  3) WebhookList — 정책 4 입력 + endpoint 표.
 *     `?webhooks=loading|empty|error` 쿼리로 4-variant 검증.
 *  4) TokenRotateModal (BIZ-109 P1) — 키워드 + 카운트다운 ConfirmGate.
 *  5) WebhookEditModal (BIZ-109 P1) — URL/시크릿/정책/Body 스키마 편집.
 *  6) TrafficSimulationModal (BIZ-109 P1, DESIGN.md §3.4) — WebhookGuardCard
 *     기반 미리보기 + 처리/대기/거부 메트릭.
 *
 * 본 단계는 정적 fixture 만 사용 — 데이터 소스 연결은 데몬 통합 단계에서 교체.
 * 토글/저장/회전 등은 로컬 state 만 갱신하고 console 로 박제 (실제 mutation 은 후속 sub-issue).
 */
"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { findAreaByPath } from "@/app/areas";
import { TelegramCard } from "./_components/TelegramCard";
import {
  WebhookList,
  type WebhookListState,
} from "./_components/WebhookList";
import { TokenRotateModal } from "./_components/TokenRotateModal";
import { WebhookEditModal } from "./_components/WebhookEditModal";
import { TrafficSimulationModal } from "./_components/TrafficSimulationModal";
import {
  getChannelsSnapshot,
  type WebhookEndpoint,
  type WebhookPolicy,
} from "./_data";

const VALID_LIST_STATES: readonly WebhookListState[] = [
  "default",
  "empty",
  "loading",
  "error",
];

export default function ChannelsPage() {
  return (
    <Suspense fallback={null}>
      <ChannelsContent />
    </Suspense>
  );
}

function ChannelsContent() {
  const area = findAreaByPath("/channels");
  const snapshot = useMemo(() => getChannelsSnapshot(), []);

  const params = useSearchParams();
  const webhookState = readState(params.get("webhooks"));

  // Telegram allowlist 는 ", " join 된 문자열로 controlled.
  const [allowlistInput, setAllowlistInput] = useState(
    snapshot.telegram.allowlist.join(", "),
  );
  const [policy, setPolicy] = useState<WebhookPolicy>(
    () => ({ ...snapshot.webhooks.policy }),
  );
  const [endpoints, setEndpoints] = useState<WebhookEndpoint[]>(() =>
    snapshot.webhooks.endpoints.map((e) => ({ ...e })),
  );

  // 모달 open 상태는 페이지가 보유 — 모달 자체는 controlled.
  const [rotateOpen, setRotateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<WebhookEndpoint | null>(null);
  const [simTarget, setSimTarget] = useState<WebhookEndpoint | null>(null);

  // 4-variant 적용 — empty 면 endpoints 비우기. error/loading 시 endpoints 는
  // 표시되지 않으므로 fixture 그대로 둔다.
  const webhooksForRender = {
    ...snapshot.webhooks,
    policy,
    endpoints: webhookState === "empty" ? [] : endpoints,
  };

  return (
    <section
      className="mx-auto flex max-w-6xl flex-col gap-6 p-8"
      data-testid="channels-page"
    >
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold text-(--foreground-strong)">
          {area?.label ?? "채널"}
        </h1>
        <p className="text-sm text-(--muted-foreground)">
          Telegram 봇 · 웹훅 endpoint 와 정책을 관리합니다.
        </p>
      </header>

      <TelegramCard
        channel={snapshot.telegram}
        allowlistInput={allowlistInput}
        onAllowlistChange={setAllowlistInput}
        onRotateToken={() => setRotateOpen(true)}
        onSendTest={() => {
          if (typeof console !== "undefined") {
            console.info("[channels] telegram test message", allowlistInput);
          }
        }}
        onSave={() => {
          if (typeof console !== "undefined") {
            console.info("[channels] telegram save", allowlistInput);
          }
        }}
      />

      <WebhookList
        state={webhookState}
        webhooks={webhooksForRender}
        policy={policy}
        onPolicyChange={setPolicy}
        onToggleEndpoint={(id, next) => {
          setEndpoints((cur) =>
            cur.map((e) => (e.id === id ? { ...e, enabled: next } : e)),
          );
          if (typeof console !== "undefined") {
            console.info("[channels] webhook toggle", id, next);
          }
        }}
        onEditEndpoint={(endpoint) => setEditTarget(endpoint)}
        onRetry={() => {
          if (typeof console !== "undefined") {
            console.info("[channels] webhook retry");
          }
        }}
      />

      <TokenRotateModal
        open={rotateOpen}
        targetLabel="Telegram Bot"
        onClose={() => setRotateOpen(false)}
        onConfirm={() => {
          if (typeof console !== "undefined") {
            console.info("[channels] telegram token rotate");
          }
        }}
      />

      <WebhookEditModal
        open={editTarget !== null}
        endpoint={editTarget}
        onClose={() => setEditTarget(null)}
        onSubmit={(id, next) => {
          setEndpoints((cur) =>
            cur.map((e) => (e.id === id ? { ...next } : e)),
          );
          if (typeof console !== "undefined") {
            console.info("[channels] webhook edit save", id, next);
          }
        }}
        onOpenSimulation={(endpoint) => {
          // 편집 모달은 시뮬 모달이 닫힐 때까지 백그라운드로 잔존 — 시뮬 결과를
          // 본 후 즉시 저장 흐름을 이어 갈 수 있도록 시뮬을 위로 띄운다.
          setSimTarget(endpoint);
        }}
      />

      <TrafficSimulationModal
        open={simTarget !== null}
        endpoint={simTarget}
        onClose={() => setSimTarget(null)}
      />
    </section>
  );
}

/** ?webhooks=foo 의 4-variant 매핑 — 알 수 없으면 default. */
function readState(raw: string | null): WebhookListState {
  if (raw && (VALID_LIST_STATES as readonly string[]).includes(raw)) {
    return raw as WebhookListState;
  }
  return "default";
}
