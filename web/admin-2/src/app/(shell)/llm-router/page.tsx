/**
 * /llm-router — Admin 2.0 S4 (BIZ-115).
 *
 * admin.pen `BBA7M` (LLM Router Shell) 의 콘텐츠 영역을 React 로 박제한다.
 * 구성 (위 → 아래):
 *  1) 페이지 헤더 — h1 "LLM 라우터" + 한 줄 설명 + 기본 라우터 정보.
 *  2) ProvidersGrid — 프로바이더 카드 그리드.
 *     `?providers=loading|empty|error` 쿼리로 4-variant 검증.
 *  3) FallbackChainCard — 1순위 → 2순위 → 3순위 pill 행.
 *  4) RoutingRulesCard — 라우팅 규칙 목록 + Add/Edit 트리거.
 *  5) Add/Edit Provider · Routing Rule Editor 3개 모달.
 *
 * 본 단계는 정적 fixture 만 사용 — 데이터 소스 연결은 데몬 통합 단계에서 교체.
 * 모달의 onSubmit/onDryRun/onDelete 등은 console 로그로만 박제 — 실제 mutation 은 후속 sub-issue.
 */
"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { findAreaByPath } from "@/app/areas";
import { Badge } from "@/design/atoms/Badge";
import { ProvidersGrid, type ProvidersState } from "./_components/ProvidersGrid";
import { FallbackChainCard } from "./_components/FallbackChainCard";
import { RoutingRulesCard } from "./_components/RoutingRulesCard";
import { AddProviderModal } from "./_components/AddProviderModal";
import { EditProviderModal } from "./_components/EditProviderModal";
import { RoutingRuleEditorModal } from "./_components/RoutingRuleEditorModal";
import {
  getRouterSnapshot,
  type RouterProvider,
  type RoutingRule,
} from "./_data";

const VALID_STATES: readonly ProvidersState[] = [
  "default",
  "empty",
  "loading",
  "error",
];

export default function LlmRouterPage() {
  return (
    <Suspense fallback={null}>
      <LlmRouterContent />
    </Suspense>
  );
}

function LlmRouterContent() {
  const area = findAreaByPath("/llm-router");
  const snapshot = getRouterSnapshot();

  // ?providers=loading|empty|error 로 ProvidersGrid 의 4-variant 를
  // e2e/시각 검증할 수 있게 한다 (DESIGN.md §1 Principle 3).
  const params = useSearchParams();
  const requested = params.get("providers");
  const gridState: ProvidersState = (
    requested && (VALID_STATES as readonly string[]).includes(requested)
      ? requested
      : "default"
  ) as ProvidersState;

  // empty variant 일 때는 fixture 비우기 — variant 별 카드 표현이 일관되도록.
  const providers = gridState === "empty" ? [] : snapshot.providers;

  // 모달 open 상태는 페이지가 보유 — 모달 자체는 controlled.
  const [addOpen, setAddOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<RouterProvider | null>(null);
  const [ruleTarget, setRuleTarget] = useState<RoutingRule | null>(null);

  const defaultProvider =
    snapshot.providers.find((p) => p.id === snapshot.defaultProviderId) ?? null;

  return (
    <section
      className="mx-auto flex max-w-6xl flex-col gap-6 p-8"
      data-testid="llm-router-page"
    >
      <header className="flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <div className="flex flex-col gap-1">
            <h1 className="text-2xl font-semibold text-(--foreground-strong)">
              {area?.label ?? "LLM 라우터"}
            </h1>
            <p className="text-sm text-(--muted-foreground)">
              프로바이더·fallback 체인·라우팅 정책을 관리합니다 (DESIGN.md §4.3).
            </p>
          </div>
          {defaultProvider ? (
            <div
              data-testid="llm-router-default"
              className="flex items-center gap-2 text-xs text-(--muted-foreground)"
            >
              <span>기본 라우터</span>
              <Badge tone="brand">{defaultProvider.name}</Badge>
              <span className="font-mono">{defaultProvider.model}</span>
            </div>
          ) : null}
        </div>
      </header>

      <ProvidersGrid
        state={gridState}
        providers={providers}
        onEdit={(p) => setEditTarget(p)}
        onAdd={() => setAddOpen(true)}
        onRetry={() => {
          /* mock — 데몬 통합 단계에서 실제 refetch. */
          if (typeof console !== "undefined") {
            console.info("[llm-router] retry providers fetch");
          }
        }}
      />

      <FallbackChainCard
        chain={snapshot.fallbackChain}
        providers={snapshot.providers}
        onAdd={() => setAddOpen(true)}
      />

      <RoutingRulesCard
        rules={snapshot.rules}
        onAdd={() => {
          // 본 단계는 "신규 규칙" 폼이 별도로 없고, 첫 규칙을 prefill 한 편집 흐름으로 박제.
          if (snapshot.rules[0]) {
            setRuleTarget(snapshot.rules[0]);
          }
        }}
        onEdit={(rule) => setRuleTarget(rule)}
      />

      <AddProviderModal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onSubmit={(value) => {
          if (typeof console !== "undefined") {
            console.info("[llm-router] add provider", value);
          }
        }}
      />

      <EditProviderModal
        open={editTarget !== null}
        provider={editTarget}
        onClose={() => setEditTarget(null)}
        onSubmit={(value) => {
          if (typeof console !== "undefined") {
            console.info("[llm-router] edit provider", value);
          }
        }}
        onRotateSecret={(id) => {
          if (typeof console !== "undefined") {
            console.info("[llm-router] rotate secret", id);
          }
        }}
        onDelete={(id) => {
          if (typeof console !== "undefined") {
            console.info("[llm-router] delete provider", id);
          }
        }}
      />

      <RoutingRuleEditorModal
        open={ruleTarget !== null}
        rule={ruleTarget}
        onClose={() => setRuleTarget(null)}
        onDryRun={(value) => {
          if (typeof console !== "undefined") {
            console.info("[llm-router] dry-run rule", value);
          }
        }}
      />
    </section>
  );
}
