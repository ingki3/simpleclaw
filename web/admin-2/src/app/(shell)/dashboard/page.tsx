/**
 * /dashboard — Admin 2.0 S3 (BIZ-114).
 *
 * admin.pen `xNjlT` (Dashboard Shell) 의 콘텐츠 영역을 React 로 박제한다.
 * 구성 (위 → 아래):
 *  1) 페이지 헤더 — h1 "대시보드" + 한 줄 설명 + 4도메인 헬스 띠.
 *  2) DashboardMetrics — 4-카드 metric 그리드 (24h 메시지 / 토큰 / 활성 알람 / 가동 시간).
 *  3) ActiveProjectsPanel — admin.pen `XFipm` BIZ-66 패널 mirror.
 *     `?activeProjects=loading|empty|error` 쿼리로 4-variant 검증.
 *  4) RecentActivityCard + RecentAlertsCard — 2열 그리드 (최근 변경 / 최근 에러).
 *
 * 본 단계는 정적 fixture 만 사용 — 데이터 소스 연결은 S5/S6/S13 에서 교체.
 */
"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { findAreaByPath } from "@/app/areas";
import { SystemStatusRow } from "./_components/SystemStatusRow";
import { DashboardMetrics } from "./_components/DashboardMetrics";
import {
  ActiveProjectsPanel,
  type ActiveProjectsState,
} from "./_components/ActiveProjectsPanel";
import { RecentActivityCard } from "./_components/RecentActivityCard";
import { RecentAlertsCard } from "./_components/RecentAlertsCard";
import { getDashboardSnapshot } from "./_data";

const VALID_AP_STATES: readonly ActiveProjectsState[] = [
  "default",
  "empty",
  "loading",
  "error",
];

export default function DashboardPage() {
  return (
    <Suspense fallback={null}>
      <DashboardContent />
    </Suspense>
  );
}

function DashboardContent() {
  const area = findAreaByPath("/dashboard");
  const snapshot = getDashboardSnapshot();

  // ?activeProjects=loading|empty|error 로 ActiveProjectsPanel 의 4-variant 를
  // e2e/시각 검증할 수 있게 한다 (DESIGN.md §1 Principle 3).
  const params = useSearchParams();
  const requested = params.get("activeProjects");
  const apState: ActiveProjectsState = (
    requested && (VALID_AP_STATES as readonly string[]).includes(requested)
      ? requested
      : "default"
  ) as ActiveProjectsState;

  return (
    <section
      className="mx-auto flex max-w-6xl flex-col gap-6 p-8"
      data-testid="dashboard-page"
    >
      <header className="flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <div className="flex flex-col gap-1">
            <h1 className="text-2xl font-semibold text-(--foreground-strong)">
              {area?.label ?? "대시보드"}
            </h1>
            <p className="text-sm text-(--muted-foreground)">
              운영 한눈에 보기 — 데몬·LLM·웹훅·크론 4영역의 헬스와 최근 변경/알림.
            </p>
          </div>
          <SystemStatusRow domains={snapshot.domains} />
        </div>
      </header>

      <DashboardMetrics metrics={snapshot.metrics} />

      <ActiveProjectsPanel
        state={apState}
        projects={snapshot.activeProjects}
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <RecentActivityCard entries={snapshot.recentChanges} />
        <RecentAlertsCard alerts={snapshot.alerts} />
      </div>
    </section>
  );
}
