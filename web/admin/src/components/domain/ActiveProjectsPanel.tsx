"use client";

/**
 * ActiveProjectsPanel — `/memory` 화면 최상단 카드 (BIZ-96 / parent BIZ-90).
 *
 * 책임:
 *  - BIZ-66/74 의 `.agent/active_projects.jsonl` 가 산출하는 active projects 를
 *    관리형 마커와 함께 노출. "왜 USER.md 의 프로젝트 섹션이 비어 있나" 를 운영자가
 *    한 시야에서 진단할 수 있게 한다.
 *  - Gate 정책 (단발 관측 자동 승격 차단 · cluster 채택 임계) 을 풋노트로 가시화 —
 *    값 자체는 데몬이 enforcement 하므로 본 패널은 표상만 책임.
 *
 * 비책임:
 *  - mutation/triggering 없음. sidecar 갱신은 dreaming 사이클에서만 발생.
 *  - 자체 polling 없음. 상위 페이지가 dreaming 종료 시점에 ``refreshKey`` 를 +1 해
 *    재조회를 유도한다 — DreamingObservabilityPanel 과 동일한 패턴.
 *
 * 디자인 결정:
 *  - 색은 spec 의 ``$--info-soft`` 가 admin 토큰 명세에는 없어 등가물인
 *    ``--color-info-bg`` (light: ``#e5f4fb`` / dark: ``#06243a``) 와 ``--color-info``
 *    텍스트로 매핑. WCAG AA 4.5:1 은 light 가 8.6:1, dark 가 9.4:1 (Badge.tsx 와 동일).
 *  - 한 행이 768px 미만에서도 잘리지 않도록 ``flex-wrap`` 으로 우아하게 줄바꿈.
 *  - 빈 상태는 명시 문구 — "managed 가 비어있다" 자체가 BIZ-66 진단에 의미 있는 신호.
 */

import { useCallback, useEffect, useState } from "react";
import { Layers, RefreshCw } from "lucide-react";
import { Button } from "@/components/atoms/Button";
import {
  type ActiveProjectSummary,
  type ActiveProjectsResponse,
  getActiveProjects,
} from "@/lib/api/memory";
import { cn } from "@/lib/cn";

export interface ActiveProjectsPanelProps {
  /** 외부 변화 시 +1 → 패널이 자동 재조회. */
  refreshKey?: number;
}

interface PanelState {
  data: ActiveProjectsResponse | null;
  loading: boolean;
  error: string | null;
}

/**
 * "Nh ago" / "Nd ago" 문자열. now - updatedAt 의 분/시/일 단위 변환.
 * 음수(미래 시각)나 1분 미만은 "방금" 으로 표기 — 시계 드리프트 방어.
 */
function formatRelative(updatedAt: string, now = Date.now()): string {
  const ts = new Date(updatedAt).getTime();
  if (Number.isNaN(ts)) return "—";
  const diffMs = Math.max(0, now - ts);
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 1) return "방금";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function ActiveProjectsPanel({ refreshKey }: ActiveProjectsPanelProps) {
  const [state, setState] = useState<PanelState>({
    data: null,
    loading: true,
    error: null,
  });

  const refresh = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: null }));
    try {
      const data = await getActiveProjects();
      setState({ data, loading: false, error: null });
    } catch (e) {
      setState({
        data: null,
        loading: false,
        error: e instanceof Error ? e.message : String(e),
      });
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh, refreshKey]);

  const projects = state.data?.active_projects ?? [];
  const policy = state.data?.gate_policy;

  return (
    <section
      aria-labelledby="active-projects-title"
      className="flex flex-col gap-4 rounded-(--radius-l) border border-(--border) bg-(--card) p-6"
      style={{ minHeight: 120 }}
    >
      <header className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h2
            id="active-projects-title"
            className="flex items-center gap-2 text-base font-semibold text-(--foreground-strong)"
          >
            <Layers size={16} aria-hidden /> Active Projects · 자동 관리
          </h2>
          <p className="text-xs text-(--muted-foreground)">
            managed marker 보존 · 단발 관측 자동 승격 차단 (Gate)
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => void refresh()}
          leftIcon={<RefreshCw size={14} aria-hidden />}
          disabled={state.loading}
          aria-label="Active Projects 새로고침"
        >
          새로고침
        </Button>
      </header>

      {state.error ? (
        <div
          role="alert"
          className="rounded-(--radius-m) border border-(--color-error) bg-(--color-error-bg) px-3 py-2 text-xs text-(--color-error)"
        >
          Active Projects 를 불러오지 못했습니다: {state.error}
        </div>
      ) : state.loading && !state.data ? (
        <ul aria-busy="true" aria-label="로딩 중" className="flex flex-col gap-2">
          {[0, 1, 2].map((i) => (
            <li
              key={i}
              className="flex items-center gap-3 rounded-(--radius-m) bg-(--surface) px-3 py-2"
            >
              <span className="h-3 w-3 animate-pulse rounded-full bg-(--border)" />
              <span className="h-3 w-40 animate-pulse rounded bg-(--border)" />
              <span className="h-3 w-12 animate-pulse rounded bg-(--border)" />
            </li>
          ))}
        </ul>
      ) : projects.length === 0 ? (
        <div className="rounded-(--radius-m) border border-dashed border-(--border) bg-(--surface) px-4 py-5 text-center text-xs text-(--muted-foreground)">
          아직 managed 프로젝트가 없습니다. dreaming 이 cluster 채택 임계를 통과하면
          여기에 표시됩니다.
        </div>
      ) : (
        <ul className="flex flex-col gap-2">
          {projects.map((p) => (
            <ActiveProjectRow key={p.id} project={p} />
          ))}
        </ul>
      )}

      <footer className="text-[11px] leading-relaxed text-(--muted-foreground)">
        BIZ-66 · managed marker 가 있는 항목은 dreaming decay 에서 제외됩니다.
        단발 관측은 cluster 채택 임계
        {policy ? ` (${policy.cluster_threshold.toFixed(2)})` : ""} 미만이면 후보 큐로
        보내고 자동 승격하지 않습니다.
      </footer>
    </section>
  );
}

interface ActiveProjectRowProps {
  project: ActiveProjectSummary;
}

function ActiveProjectRow({ project }: ActiveProjectRowProps) {
  const isManaged = project.managed;
  // bullet 색: managed 는 info, 그 외(향후 decay 후보 등) 는 muted.
  const bulletClass = isManaged ? "text-(--color-info)" : "text-(--muted-foreground)";

  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-(--radius-m) bg-(--surface) px-3 py-2">
      <span aria-hidden className={cn("text-sm font-medium", bulletClass)}>
        •
      </span>
      <span className="text-[13px] font-medium text-(--foreground-strong)">
        {project.title}
      </span>
      {isManaged ? (
        <span
          aria-label="dreaming decay 제외"
          className="inline-flex items-center rounded-(--radius-pill) bg-(--color-info-bg) px-2 py-0.5 text-[11px] font-medium text-(--color-info)"
        >
          managed
        </span>
      ) : null}
      <span className="text-[13px] font-medium tabular-nums text-(--foreground-strong)">
        {project.score.toFixed(2)}
      </span>
      <span className="text-xs text-(--muted-foreground)">
        · updated {formatRelative(project.updated_at)}
      </span>
    </li>
  );
}

// 테스트용 export.
export { formatRelative };
