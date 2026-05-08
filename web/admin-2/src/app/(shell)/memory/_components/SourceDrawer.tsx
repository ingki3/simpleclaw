"use client";

/**
 * SourceDrawer — admin.pen `ftlSL` (Screen 06.5-B · Source Drawer) 박제.
 *
 * 인사이트 한 건의 근거 메시지 (대화/voice/cron 로그) 를 우측 슬라이드 패널로
 * 보여준다. Modal 보다 본문 영역을 가리지 않도록 우측 480px 고정 폭 — Skills &
 * Recipes 의 SkillDiscoveryDrawer 와 동일한 시각 spec.
 *
 * 출처 메타: 채널 / role / 시각 + 원문 링크 (있는 경우 새 탭으로 이동).
 * 본문은 whitespace 보존 + 자동 줄바꿈.
 *
 * ESC / 백드롭 클릭으로 닫는다 — Modal 패턴과 일관.
 */

import { useEffect, useMemo } from "react";
import { Badge } from "@/design/atoms/Badge";
import { EmptyState } from "@/design/molecules/EmptyState";
import { cn } from "@/lib/cn";
import type { MemoryInsight, SourceMessage } from "../_data";

interface SourceDrawerProps {
  /** 열린 인사이트 — null 이면 드로어가 닫혀 있다. */
  insight: MemoryInsight | null;
  /** 인사이트의 근거 메시지 — 비어있으면 빈 안내. */
  messages: readonly SourceMessage[];
  onClose: () => void;
}

export function SourceDrawer({
  insight,
  messages,
  onClose,
}: SourceDrawerProps) {
  // ESC 로 닫기 — Modal 패턴과 일관.
  useEffect(() => {
    if (!insight) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [insight, onClose]);

  // 채널 메타 — 메시지에서 unique 채널 목록 추출 (헤더 보조 정보).
  const channels = useMemo(() => {
    if (!insight) return [] as string[];
    const set = new Set<string>();
    for (const m of messages) set.add(m.channel);
    return Array.from(set);
  }, [insight, messages]);

  if (!insight) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="인사이트 출처"
      data-testid="source-drawer"
      className="fixed inset-0 z-40 flex justify-end bg-black/40"
      onClick={onClose}
    >
      <aside
        onClick={(e) => e.stopPropagation()}
        className={cn(
          "flex h-full w-full max-w-md flex-col gap-4 border-l border-(--border)",
          "bg-(--card-elevated) shadow-[var(--shadow-l)]",
        )}
      >
        <header className="flex items-start justify-between gap-3 border-b border-(--border) px-5 py-4">
          <div className="flex flex-col gap-1">
            <h2 className="text-lg font-semibold text-(--foreground-strong)">
              인사이트 출처
            </h2>
            <p className="text-xs text-(--muted-foreground)">
              dreaming 이 사용한 근거 메시지 — 원문 그대로.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="닫기"
            data-testid="source-drawer-close"
            className="-mr-2 -mt-1 inline-flex h-8 w-8 items-center justify-center rounded-(--radius-m) text-(--muted-foreground) hover:bg-(--surface) hover:text-(--foreground)"
          >
            ×
          </button>
        </header>

        <div className="px-5">
          <div
            data-testid="source-drawer-meta"
            className="flex flex-col gap-2 rounded-(--radius-m) border border-(--border) bg-(--surface) p-3 text-xs"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-(--muted-foreground)">
                {insight.topic}
              </span>
              <Badge tone="brand" size="sm">
                {insight.lifecycle}
              </Badge>
              <Badge tone="neutral" size="sm">
                근거 {insight.evidenceCount}건
              </Badge>
              {channels.map((ch) => (
                <Badge key={ch} tone="info" size="sm">
                  {ch}
                </Badge>
              ))}
            </div>
            <p className="text-(--foreground)">{insight.text}</p>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-5 pb-5">
          {messages.length === 0 ? (
            <div data-testid="source-drawer-empty">
              <EmptyState
                title="근거 메시지를 찾을 수 없어요"
                description="원본 대화가 보관 주기를 지나 아카이브된 상태일 수 있어요. dreaming 사이클 직후라면 잠시 뒤 다시 시도해 주세요."
              />
            </div>
          ) : (
            <ol
              data-testid="source-drawer-messages"
              className="flex flex-col gap-2"
            >
              {messages.map((m) => (
                <li
                  key={m.id}
                  data-testid={`source-message-${m.id}`}
                  className="rounded-(--radius-m) border border-(--border) bg-(--surface) px-3 py-2 text-sm"
                >
                  <div className="flex items-center justify-between text-[10px] font-mono text-(--muted-foreground)">
                    <span>
                      {m.role} · {m.channel}
                    </span>
                    <span>{new Date(m.timestamp).toLocaleString("ko-KR")}</span>
                  </div>
                  <p className="mt-1 whitespace-pre-wrap break-words text-(--foreground)">
                    {m.content}
                  </p>
                  {m.permalink ? (
                    <a
                      href={m.permalink}
                      target="_blank"
                      rel="noreferrer noopener"
                      data-testid={`source-message-${m.id}-permalink`}
                      className="mt-1 inline-flex text-xs text-(--primary) hover:underline"
                    >
                      원문 보기 ↗
                    </a>
                  ) : null}
                </li>
              ))}
            </ol>
          )}
        </div>
      </aside>
    </div>
  );
}
