"use client";

/**
 * Logs & Traces — admin.pen Screen 09 / docs/admin-requirements.md §2.
 *
 * 화면 책임:
 *  1) ``/admin/v1/logs``를 폴링해 최신 로그를 가상 스크롤 리스트로 노출.
 *  2) 레벨/모듈/검색 필터를 URL 쿼리스트링에 동기화 — 링크로 공유 가능.
 *  3) 자동 새로고침(1초) 토글: ON일 때 새 항목은 위에서 페이드인.
 *  4) 행 클릭 → trace_id로 묶인 항목들을 우측 Drawer에 펼쳐 JSON 원문 노출.
 *  5) 로그 레벨 설정 패널 — 백엔드가 ``logging.level`` PATCH를 지원하지 않으면
 *     "준비 중" 상태로 표시(BIZ-37 후속 작업과 자연스럽게 합류).
 *
 * 데이터 흐름의 단순화:
 *  - 백엔드는 ``trace_id`` / ``level`` / ``module`` 필터만 서버 측에서 적용한다.
 *  - 자유 검색은 클라이언트 substring (``entryMatchesSearch``).
 *  - "무한 스크롤"은 cursor 기반이 아니라 ``limit``을 200씩 키우는 단순 전략이다 —
 *    백엔드가 cursor를 도입하면 그때 교체. 가상 스크롤이 메모리·렌더 비용을 흡수한다.
 *
 * URL 동기화 정책:
 *  - 변경은 즉시 ``router.replace``로 push 없이 history 한 줄에 누적되지 않게 한다.
 *  - ``trace_id`` 쿼리가 페이지 진입 시점에 있으면 자동으로 Drawer를 연다 — 대시보드의
 *    "trace 보기" 같은 다른 화면이 ``/logs?trace_id=...``로 깊은 링크를 보내는 흐름을 지원.
 */

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAdminResource } from "@/lib/api/use-admin-resource";
import { VirtualLogList } from "@/components/molecules/VirtualLogList";
import { LogFilters } from "./_components/LogFilters";
import { LogRow } from "./_components/LogRow";
import { TraceDrawer } from "./_components/TraceDrawer";
import { LogLevelControl } from "./_components/LogLevelControl";
import {
  buildLogsPath,
  entryKey,
  entryMatchesSearch,
  parseLevelToken,
  type LogApiEntry,
  type LogLevelToken,
  type LogsResponse,
} from "@/lib/api/logs";

// 한 번에 가져올 최대 항목 수의 단계 — "더 보기" 1회당 증가폭.
const PAGE_STEP = 200;
const INITIAL_LIMIT = 200;
const AUTO_REFRESH_INTERVAL_MS = 1000;
const VIRTUAL_LIST_HEIGHT = 540;
const ROW_HEIGHT = 44;

export default function LogsPage() {
  // useSearchParams는 Suspense 경계가 필요 — Next 15 SSG 호환.
  return (
    <Suspense fallback={<LogsLoadingShell />}>
      <LogsPageBody />
    </Suspense>
  );
}

function LogsLoadingShell() {
  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-3xl font-semibold leading-tight text-(--foreground-strong)">
          로그
        </h1>
        <p className="text-sm text-(--muted-foreground)">로그를 불러오는 중…</p>
      </header>
    </div>
  );
}

function LogsPageBody() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // ─────────── URL → state ───────────
  const urlLevel = parseLevelToken(searchParams.get("level"));
  const urlModule = searchParams.get("module") ?? "";
  const urlSearch = searchParams.get("q") ?? "";
  const urlTraceId = searchParams.get("trace_id") ?? "";

  const [autoRefresh, setAutoRefresh] = useState(true);
  const [limit, setLimit] = useState(INITIAL_LIMIT);
  // 검색은 입력 즉시 URL에 반영하면 캐럿이 튀고 history도 어수선해진다 → 200ms debounce.
  const [searchInput, setSearchInput] = useState(urlSearch);
  const [moduleInput, setModuleInput] = useState(urlModule);

  // 입력 변경 시 URL을 갱신.
  useEffect(() => {
    const handle = window.setTimeout(() => {
      updateQueryParams(router, searchParams, {
        q: searchInput || undefined,
        module: moduleInput || undefined,
      });
    }, 200);
    return () => window.clearTimeout(handle);
    // searchParams 객체는 매 렌더 새로 생성되지만 toString이 같으면 동일 — 체이닝으로 비교.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchInput, moduleInput]);

  // 외부에서 trace_id가 들어왔을 때 Drawer 자동 오픈.
  const [drawerOpen, setDrawerOpen] = useState(Boolean(urlTraceId));
  const [selected, setSelected] = useState<LogApiEntry | null>(
    urlTraceId
      ? ({ trace_id: urlTraceId } as LogApiEntry)
      : null,
  );

  // 폴링 path — level/module은 서버 필터, q는 클라이언트 필터.
  const path = useMemo(
    () =>
      buildLogsPath({
        limit,
        level: urlLevel,
        module: urlModule || undefined,
        // trace_id는 Drawer 전용 — 메인 리스트에서는 항상 전체를 본다.
      }),
    [limit, urlLevel, urlModule],
  );

  const { data, error, isLoading, isRefreshing, refetch } =
    useAdminResource<LogsResponse>(path, {
      intervalMs: autoRefresh ? AUTO_REFRESH_INTERVAL_MS : 0,
    });

  // 응답은 시간 오름차순 — 가장 최신 항목이 위에 오도록 reverse.
  // 클라이언트 자유 검색을 함께 적용한다.
  const entries: LogApiEntry[] = useMemo(() => {
    const list = data?.entries ?? [];
    const filtered = urlSearch
      ? list.filter((e) => entryMatchesSearch(e, urlSearch))
      : list;
    // 새 배열을 만들어 reverse — 원본 mutation 회피.
    return filtered.slice().reverse();
  }, [data, urlSearch]);

  // 새 도착 항목 강조용 — 이전 렌더 키 셋을 추적.
  const prevKeysRef = useRef<Set<string>>(new Set());
  const freshKeys = useMemo<Set<string>>(() => {
    const next = new Set<string>();
    const prev = prevKeysRef.current;
    const fresh = new Set<string>();
    for (let i = 0; i < entries.length; i += 1) {
      const k = entryKey(entries[i], i);
      next.add(k);
      // 첫 로드(prev 비었음)는 모두 fresh로 표시하지 않는다 — 시각적 노이즈 방지.
      if (prev.size > 0 && !prev.has(k)) fresh.add(k);
    }
    prevKeysRef.current = next;
    return fresh;
  }, [entries]);

  // 무한 스크롤 — 컨테이너 바닥에 가까워지면 limit 키우기.
  const onListScroll = useCallback(
    (event: React.UIEvent<HTMLDivElement>) => {
      const node = event.currentTarget;
      const remaining = node.scrollHeight - node.scrollTop - node.clientHeight;
      if (remaining < ROW_HEIGHT * 4 && entries.length >= limit) {
        // 도달했고 아직 백엔드에 더 있을 가능성 → limit 증가.
        setLimit((cur) => cur + PAGE_STEP);
      }
    },
    [entries.length, limit],
  );

  return (
    <div className="flex flex-col gap-6" onScrollCapture={onListScroll}>
      <header className="flex flex-col gap-1">
        <h1 className="text-3xl font-semibold leading-tight text-(--foreground-strong)">
          로그
        </h1>
        <p className="text-sm text-(--muted-foreground)">
          구조화 로그 스트림 · 트레이스 타임라인 · 실시간 자동 새로고침.
        </p>
      </header>

      <LogLevelControl />

      <LogFilters
        level={urlLevel}
        onLevelChange={(next) =>
          updateQueryParams(router, searchParams, { level: next })
        }
        module={moduleInput}
        onModuleChange={setModuleInput}
        search={searchInput}
        onSearchChange={setSearchInput}
        autoRefresh={autoRefresh}
        onAutoRefreshChange={setAutoRefresh}
        onRefreshNow={refetch}
        isRefreshing={isRefreshing || isLoading}
      />

      <section
        aria-label="로그 스트림"
        className="flex flex-col gap-2"
      >
        <div className="flex items-center justify-between text-xs text-(--muted-foreground)">
          <span>
            {error ? (
              <span className="text-(--color-error)">
                로그를 불러오지 못했습니다 — {error.message}
              </span>
            ) : (
              <>
                {entries.length.toLocaleString("ko-KR")}개 표시
                {entries.length >= limit ? ` (limit ${limit})` : null}
              </>
            )}
          </span>
          {entries.length >= limit ? (
            <button
              type="button"
              className="text-(--primary) hover:underline"
              onClick={() => setLimit((cur) => cur + PAGE_STEP)}
            >
              더 보기 (+{PAGE_STEP})
            </button>
          ) : null}
        </div>
        <VirtualLogList<LogApiEntry>
          items={entries}
          rowHeight={ROW_HEIGHT}
          height={VIRTUAL_LIST_HEIGHT}
          getKey={(item, idx) => entryKey(item, idx)}
          renderRow={(item, idx) => (
            <LogRow
              entry={item}
              onSelect={() => {
                setSelected(item);
                setDrawerOpen(true);
                // 깊은 링크 공유를 위해 trace_id를 URL에 반영(있을 때만).
                if (item.trace_id) {
                  updateQueryParams(router, searchParams, {
                    trace_id: item.trace_id,
                  });
                }
              }}
              isFresh={freshKeys.has(entryKey(item, idx))}
            />
          )}
          emptyState={
            isLoading ? (
              <span>로그를 불러오는 중…</span>
            ) : (
              <span>표시할 로그가 없습니다.</span>
            )
          }
        />
      </section>

      <TraceDrawer
        open={drawerOpen}
        onOpenChange={(open) => {
          setDrawerOpen(open);
          if (!open) {
            // 닫을 때 URL의 trace_id를 정리해 깊은 링크가 stale하지 않도록.
            updateQueryParams(router, searchParams, { trace_id: undefined });
          }
        }}
        selected={selected}
      />
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// URL 동기화 유틸
// ──────────────────────────────────────────────────────────────────

type ParamPatch = Record<string, string | undefined>;

/**
 * 현재 ``searchParams``에 ``patch``를 머지해 ``router.replace``로 적용한다.
 * 값이 ``undefined``면 키 자체를 제거한다.
 */
function updateQueryParams(
  router: ReturnType<typeof useRouter>,
  current: URLSearchParams | ReadonlyURLSearchParamsLike,
  patch: ParamPatch,
): void {
  const next = new URLSearchParams(current.toString());
  for (const [k, v] of Object.entries(patch)) {
    if (v === undefined || v === "") next.delete(k);
    else next.set(k, v);
  }
  const qs = next.toString();
  router.replace(qs ? `?${qs}` : "?", { scroll: false });
}

// next/navigation의 ReadonlyURLSearchParams는 toString만 보장 — 본 시그니처로 약속.
interface ReadonlyURLSearchParamsLike {
  toString(): string;
}
