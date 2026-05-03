"use client";

/**
 * Persona 화면 — admin.pen Screen 03 / DESIGN.md §4.1·§4.3·§4.9.
 *
 * 4개 페르소나 파일(SOUL/AGENT/USER/MEMORY)을 탭으로 전환하면서 편집한다.
 * 각 탭은 좌(textarea)/우(미리보기) 분할 — Tiptap 미채택(BIZ-38).
 *
 * 핵심 인터랙션:
 *  - 탭 전환 시 미저장 변경이 있으면 confirm 다이얼로그
 *  - 토큰 카운트 배지: 합산 한도 초과 시 destructive-soft 색
 *  - 저장 = hot-reload (♻ 라벨) + 5분 undo가 달린 토스트
 *  - MEMORY 영구 삭제: ConfirmGate(파일명 입력 일치 강제)
 *  - "Resolver 미리보기" 버튼: 어셈블된 system prompt를 모달에 표시
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Eye, RefreshCcw, Save, Trash2, X } from "lucide-react";
import { Badge } from "@/components/atoms/Badge";
import { Button } from "@/components/atoms/Button";
import { ConfirmGate } from "@/components/primitives/ConfirmGate";
import { Toast } from "@/components/primitives/Toast";
import { MarkdownPreview } from "@/lib/markdown-preview";
import { cn } from "@/lib/cn";
import {
  type PersonaFileMeta,
  type PersonaFileType,
  type PersonaListResponse,
  type PersonaResolveResponse,
  deletePersona,
  listPersona,
  putPersona,
  resolvePersona,
  undoPersona,
} from "@/lib/api/persona";

type FileMap = Record<PersonaFileType, PersonaFileMeta>;

interface TabDef {
  type: PersonaFileType;
  label: string;
  filename: string;
  /** MEMORY는 영구 삭제 액션이 노출된다. */
  deletable: boolean;
}

const TABS: TabDef[] = [
  { type: "soul", label: "SOUL", filename: "SOUL.md", deletable: false },
  { type: "agent", label: "AGENT", filename: "AGENT.md", deletable: false },
  { type: "user", label: "USER", filename: "USER.md", deletable: false },
  { type: "memory", label: "MEMORY", filename: "MEMORY.md", deletable: true },
];

interface ToastState {
  tone: "success" | "warn" | "destructive-soft" | "info";
  title: string;
  description?: React.ReactNode;
  undoToken?: string;
}

export default function PersonaPage() {
  // 디스크에서 읽은 4파일 메타 — 단일 source-of-truth는 백엔드, 클라이언트는 캐시.
  const [files, setFiles] = useState<FileMap | null>(null);
  const [tokenBudget, setTokenBudget] = useState(8000);

  // 탭별 미저장 작업 본문. 탭 전환 후에도 유지되도록 분리 보관.
  const [drafts, setDrafts] = useState<Partial<Record<PersonaFileType, string>>>(
    {},
  );

  const [activeTab, setActiveTab] = useState<PersonaFileType>("soul");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [resolvePreview, setResolvePreview] = useState<
    PersonaResolveResponse | null
  >(null);
  const [resolving, setResolving] = useState(false);

  // 탭 전환 confirm용 — 사용자가 누른 다음 탭을 보관해 두고 결정 대기.
  const [pendingTab, setPendingTab] = useState<PersonaFileType | null>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data: PersonaListResponse = await listPersona();
      const map = {} as FileMap;
      for (const f of data.files) map[f.type] = f;
      setFiles(map);
      setTokenBudget(data.tokenBudget);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  // 현재 탭의 표시 텍스트 — draft가 있으면 그것을, 없으면 디스크 본문을.
  const currentDraft = useMemo(() => {
    if (!files) return "";
    return drafts[activeTab] ?? files[activeTab]?.content ?? "";
  }, [drafts, files, activeTab]);

  const dirtyTabs = useMemo(() => {
    if (!files) return new Set<PersonaFileType>();
    const out = new Set<PersonaFileType>();
    for (const t of TABS) {
      const d = drafts[t.type];
      if (d !== undefined && d !== files[t.type]?.content) out.add(t.type);
    }
    return out;
  }, [drafts, files]);

  const isCurrentDirty = dirtyTabs.has(activeTab);

  // 합산 토큰 — 탭별 draft가 있으면 그 값으로 대체해서 합산 (라이브 카운트).
  const liveTokens = useMemo(() => {
    if (!files) return { total: 0, perTab: {} as Record<PersonaFileType, number> };
    const perTab = {} as Record<PersonaFileType, number>;
    let total = 0;
    for (const t of TABS) {
      const d = drafts[t.type];
      if (d !== undefined) {
        // 클라이언트 측 동일 휴리스틱(chars/4)
        const tokens = d.length === 0 ? 0 : Math.max(1, Math.ceil(d.length / 4));
        perTab[t.type] = tokens;
      } else {
        perTab[t.type] = files[t.type]?.tokens ?? 0;
      }
      total += perTab[t.type];
    }
    return { total, perTab };
  }, [drafts, files]);

  const overBudget = liveTokens.total > tokenBudget;

  // 탭 전환 — dirty면 확인 후, 아니면 즉시.
  const requestTabChange = (next: PersonaFileType) => {
    if (next === activeTab) return;
    if (isCurrentDirty) {
      setPendingTab(next);
      return;
    }
    setActiveTab(next);
  };

  const confirmDiscardAndSwitch = () => {
    if (!pendingTab || !files) return;
    // 현재 탭 draft 폐기 → 디스크 본문으로 회귀
    setDrafts((prev) => {
      const next = { ...prev };
      delete next[activeTab];
      return next;
    });
    setActiveTab(pendingTab);
    setPendingTab(null);
  };

  // 페이지 이탈 경고 — 탭이 아닌 브라우저 nav를 막는다.
  useEffect(() => {
    if (dirtyTabs.size === 0) return;
    const onUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onUnload);
    return () => window.removeEventListener("beforeunload", onUnload);
  }, [dirtyTabs]);

  const handleSave = async () => {
    if (!files) return;
    const content = currentDraft;
    setSaving(true);
    try {
      const res = await putPersona(activeTab, content, {
        idempotencyKey: `persona-${activeTab}-${Date.now()}`,
      });
      // draft 비움 + 디스크 캐시 갱신
      setDrafts((prev) => {
        const next = { ...prev };
        delete next[activeTab];
        return next;
      });
      setFiles((prev) =>
        prev
          ? {
              ...prev,
              [activeTab]: {
                ...prev[activeTab],
                exists: true,
                content,
                tokens: res.tokens,
                updatedAt: new Date().toISOString(),
              },
            }
          : prev,
      );
      setToast({
        tone: "success",
        title: `${TABS.find((t) => t.type === activeTab)?.filename} 저장됨 ♻`,
        description:
          "다음 메시지부터 새 페르소나가 적용됩니다. 5분 안에 되돌릴 수 있어요.",
        undoToken: res.undoToken,
      });
    } catch (e) {
      setToast({
        tone: "destructive-soft",
        title: "저장에 실패했습니다",
        description: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setSaving(false);
    }
  };

  const handleUndo = async () => {
    if (!toast?.undoToken) return;
    try {
      const result = await undoPersona(toast.undoToken);
      // 디스크에서 다시 읽어 캐시 동기화
      setFiles((prev) =>
        prev
          ? {
              ...prev,
              [result.type]: {
                ...prev[result.type],
                content: result.content,
                tokens: result.tokens,
                updatedAt: new Date().toISOString(),
                exists: result.content.length > 0,
              },
            }
          : prev,
      );
      setDrafts((prev) => {
        const next = { ...prev };
        delete next[result.type];
        return next;
      });
      // 후속 토스트 — 자동 dismiss
      setToast({
        tone: "info",
        title: "되돌렸습니다",
        description: `${TABS.find((t) => t.type === result.type)?.filename}을 직전 상태로 복원했습니다.`,
      });
    } catch (e) {
      setToast({
        tone: "destructive-soft",
        title: "되돌리기 실패",
        description: e instanceof Error ? e.message : String(e),
      });
    }
  };

  const handleResolverPreview = async () => {
    setResolving(true);
    try {
      const res = await resolvePersona();
      setResolvePreview(res);
    } catch (e) {
      setToast({
        tone: "destructive-soft",
        title: "Resolver 호출 실패",
        description: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setResolving(false);
    }
  };

  const handleConfirmDelete = async () => {
    try {
      await deletePersona("memory");
      setFiles((prev) =>
        prev
          ? {
              ...prev,
              memory: {
                ...prev.memory,
                exists: false,
                content: "",
                tokens: 0,
                updatedAt: null,
              },
            }
          : prev,
      );
      setDrafts((prev) => {
        const next = { ...prev };
        delete next.memory;
        return next;
      });
      setConfirmDelete(false);
      setToast({
        tone: "warn",
        title: "MEMORY.md를 영구 삭제했습니다",
        description: "다음 메시지부터 빈 메모리로 시작합니다.",
      });
    } catch (e) {
      setConfirmDelete(false);
      setToast({
        tone: "destructive-soft",
        title: "삭제 실패",
        description: e instanceof Error ? e.message : String(e),
      });
    }
  };

  if (loading) {
    return (
      <div className="text-sm text-[--muted-foreground]">불러오는 중…</div>
    );
  }
  if (error || !files) {
    return (
      <div className="rounded-[--radius-m] border border-[--color-error] bg-[--color-error-bg] px-4 py-3 text-sm">
        페르소나 파일을 불러오지 못했습니다: {error}
      </div>
    );
  }

  const activeMeta = files[activeTab];
  const activeTabDef = TABS.find((t) => t.type === activeTab)!;

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      {/* 헤더 */}
      <header className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-[--foreground-strong]">
            페르소나
          </h1>
          <p className="mt-1 text-sm text-[--muted-foreground]">
            SOUL · AGENT · USER · MEMORY 4개 파일을 편집합니다. 저장 즉시
            <span className="ml-1 font-medium">♻ hot-reload</span>되어 다음
            메시지부터 반영돼요.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <TokenSummary
            total={liveTokens.total}
            budget={tokenBudget}
            over={overBudget}
          />
          <Button
            variant="secondary"
            size="md"
            onClick={handleResolverPreview}
            disabled={resolving}
            leftIcon={<Eye size={14} aria-hidden />}
          >
            Resolver 미리보기
          </Button>
        </div>
      </header>

      {/* 탭 바 */}
      <nav
        role="tablist"
        aria-label="페르소나 파일"
        className="flex items-center gap-1 border-b border-[--border]"
      >
        {TABS.map((t) => {
          const active = t.type === activeTab;
          const dirty = dirtyTabs.has(t.type);
          const tokens = liveTokens.perTab[t.type] ?? 0;
          return (
            <button
              key={t.type}
              role="tab"
              aria-selected={active}
              type="button"
              onClick={() => requestTabChange(t.type)}
              className={cn(
                "-mb-px flex items-center gap-2 border-b-2 px-3 py-2 text-sm font-medium transition-colors",
                active
                  ? "border-[--primary] text-[--foreground-strong]"
                  : "border-transparent text-[--muted-foreground] hover:text-[--foreground]",
              )}
            >
              <span>{t.label}</span>
              {dirty ? (
                <span
                  aria-label="저장되지 않은 변경"
                  className="inline-block h-1.5 w-1.5 rounded-[--radius-pill] bg-[--color-warning]"
                />
              ) : null}
              <Badge
                tone={overBudget ? "danger" : active ? "brand" : "neutral"}
              >
                {tokens}
              </Badge>
            </button>
          );
        })}
      </nav>

      {/* 합산 한도 초과 경고 */}
      {overBudget ? (
        <div
          role="alert"
          className="rounded-[--radius-m] border border-[--color-error] bg-[--color-error-bg] px-3 py-2 text-xs text-[--color-error]"
        >
          토큰 합산이 한도({tokenBudget})를 초과했습니다. 저장은 가능하지만
          어셈블 시 MEMORY → USER 순으로 절삭됩니다.
        </div>
      ) : null}

      {/* 메타 + 액션 */}
      <div className="flex items-center justify-between text-xs text-[--muted-foreground]">
        <div className="flex items-center gap-3">
          <span className="font-mono">{activeMeta.filename}</span>
          <span>
            {activeMeta.exists
              ? `수정: ${formatTime(activeMeta.updatedAt)}`
              : "디스크에 아직 없음 (저장 시 새로 생성)"}
          </span>
        </div>
        {activeTabDef.deletable ? (
          <Button
            variant="ghost"
            size="sm"
            leftIcon={<Trash2 size={14} aria-hidden />}
            onClick={() => setConfirmDelete(true)}
            disabled={!activeMeta.exists}
          >
            영구 삭제
          </Button>
        ) : null}
      </div>

      {/* 에디터 + 미리보기 (좌/우 분할) */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="flex flex-col gap-2">
          <div className="text-xs font-medium uppercase tracking-wide text-[--muted-foreground]">
            편집
          </div>
          <textarea
            value={currentDraft}
            onChange={(e) =>
              setDrafts((prev) => ({ ...prev, [activeTab]: e.target.value }))
            }
            spellCheck={false}
            className={cn(
              "min-h-[480px] w-full resize-y rounded-[--radius-m] border border-[--border] bg-[--card] p-3 font-mono text-sm leading-6 text-[--foreground] outline-none focus:border-[--primary]",
            )}
            placeholder={`${activeTabDef.filename} 본문을 작성하세요…`}
            aria-label={`${activeTabDef.filename} 편집`}
          />
        </div>
        <div className="flex flex-col gap-2">
          <div className="text-xs font-medium uppercase tracking-wide text-[--muted-foreground]">
            미리보기
          </div>
          <div className="min-h-[480px] rounded-[--radius-m] border border-[--border] bg-[--surface] p-4 overflow-auto">
            <MarkdownPreview source={currentDraft} />
          </div>
        </div>
      </div>

      {/* 하단 액션 바 */}
      <div className="sticky bottom-0 -mx-8 mt-2 flex items-center justify-between gap-3 border-t border-[--border] bg-[--background] px-8 py-3">
        <div className="flex items-center gap-2 text-xs text-[--muted-foreground]">
          <span
            aria-hidden
            className={cn(
              "inline-block h-2 w-2 rounded-[--radius-pill]",
              isCurrentDirty ? "bg-[--color-warning]" : "bg-[--muted-foreground]",
            )}
          />
          <span>
            {isCurrentDirty
              ? "변경사항 저장 안 됨 — 저장 시 ♻ hot-reload"
              : "저장 완료 · ♻ 다음 메시지부터 반영"}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="md"
            onClick={() =>
              setDrafts((prev) => {
                const next = { ...prev };
                delete next[activeTab];
                return next;
              })
            }
            disabled={!isCurrentDirty || saving}
            leftIcon={<RefreshCcw size={14} aria-hidden />}
          >
            되돌리기
          </Button>
          <Button
            variant="primary"
            size="md"
            onClick={handleSave}
            disabled={!isCurrentDirty || saving}
            leftIcon={<Save size={14} aria-hidden />}
          >
            {saving ? "저장 중…" : "저장 (♻ 즉시 반영)"}
          </Button>
        </div>
      </div>

      {/* Resolver 미리보기 모달 */}
      {resolvePreview ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="resolver-title"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          onClick={(e) => {
            if (e.target === e.currentTarget) setResolvePreview(null);
          }}
        >
          <div className="flex max-h-[80vh] w-full max-w-3xl flex-col rounded-[--radius-l] border border-[--border] bg-[--card] shadow-[--shadow-l]">
            <header className="flex items-center justify-between border-b border-[--border] px-5 py-3">
              <div>
                <h2
                  id="resolver-title"
                  className="text-base font-semibold text-[--foreground-strong]"
                >
                  Resolver 미리보기 (어셈블된 system prompt)
                </h2>
                <div className="mt-1 flex items-center gap-2 text-xs text-[--muted-foreground]">
                  <Badge
                    tone={
                      resolvePreview.tokenCount > resolvePreview.tokenBudget
                        ? "danger"
                        : "brand"
                    }
                  >
                    {resolvePreview.tokenCount} / {resolvePreview.tokenBudget} tokens
                  </Badge>
                  {resolvePreview.wasTruncated ? (
                    <Badge tone="warning">절삭됨</Badge>
                  ) : null}
                </div>
              </div>
              <button
                type="button"
                aria-label="닫기"
                onClick={() => setResolvePreview(null)}
                className="rounded-[--radius-sm] p-1 text-[--muted-foreground] hover:bg-[--surface]"
              >
                <X size={16} aria-hidden />
              </button>
            </header>
            <pre className="overflow-auto px-5 py-4 font-mono text-xs leading-5 text-[--foreground]">
              {resolvePreview.assembledText || "(빈 결과)"}
            </pre>
          </div>
        </div>
      ) : null}

      {/* 탭 전환 시 미저장 변경 confirm */}
      {pendingTab ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="discard-title"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          onClick={(e) => {
            if (e.target === e.currentTarget) setPendingTab(null);
          }}
        >
          <div className="w-full max-w-md rounded-[--radius-l] border border-[--border] bg-[--card] p-6 shadow-[--shadow-l]">
            <h2
              id="discard-title"
              className="text-base font-semibold text-[--foreground-strong]"
            >
              저장하지 않은 변경이 있어요
            </h2>
            <p className="mt-2 text-sm text-[--muted-foreground]">
              <span className="font-mono">{activeTabDef.filename}</span>의 변경
              사항이 저장되지 않았습니다. 다른 탭으로 이동하면 변경 사항이
              사라집니다.
            </p>
            <div className="mt-5 flex items-center justify-end gap-2">
              <Button
                variant="ghost"
                size="md"
                onClick={() => setPendingTab(null)}
              >
                여기 머무르기
              </Button>
              <Button
                variant="destructive"
                size="md"
                onClick={confirmDiscardAndSwitch}
              >
                변경 폐기하고 이동
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      {/* MEMORY 영구 삭제 ConfirmGate */}
      <ConfirmGate
        open={confirmDelete}
        title="MEMORY.md를 영구 삭제할까요?"
        description={
          <>
            지금까지 누적된 대화 기억이 모두 사라집니다. 되돌릴 수 없으며,
            <span className="font-mono"> MEMORY.md</span>를 그대로 입력해 의도를
            확인해 주세요.
          </>
        }
        expectedInput="MEMORY.md"
        confirmLabel="영구 삭제"
        onConfirm={handleConfirmDelete}
        onCancel={() => setConfirmDelete(false)}
      />

      {/* 토스트 */}
      {toast ? (
        <Toast
          tone={toast.tone}
          title={toast.title}
          description={toast.description}
          undo={
            toast.undoToken
              ? {
                  label: "되돌리기 (5분)",
                  onUndo: handleUndo,
                }
              : undefined
          }
          onClose={() => setToast(null)}
        />
      ) : null}
    </div>
  );
}

function TokenSummary({
  total,
  budget,
  over,
}: {
  total: number;
  budget: number;
  over: boolean;
}) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-[--radius-m] border px-3 py-2 text-xs",
        over
          ? "border-[--color-error] bg-[--color-error-bg] text-[--color-error]"
          : "border-[--border] bg-[--card] text-[--muted-foreground]",
      )}
      title="합산 토큰 (chars/4 근사 — 백엔드 tiktoken 연동 예정)"
    >
      <span className="font-medium text-[--foreground]">합산</span>
      <span className="font-mono">
        {total.toLocaleString()} / {budget.toLocaleString()}
      </span>
    </div>
  );
}

function formatTime(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("ko-KR", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
