/**
 * BlocklistTable — admin.pen `lVcRk` Blocklist 표 영역.
 *
 * 거절된 토픽의 차단 만료/사유를 한 화면에 노출하고, "차단 해제" 1-클릭으로
 * 다시 학습 가능 상태로 되돌린다. RejectConfirmModal 의 짝꿍 — 둘이 합쳐
 * `lVcRk` reusable 의 mirror.
 *
 * 본 단계는 데몬 미연결이므로 차단 해제는 fixture 카피만 갱신.
 */
"use client";

import { Badge } from "@/design/atoms/Badge";
import { Button } from "@/design/atoms/Button";
import { EmptyState } from "@/design/molecules/EmptyState";
import { cn } from "@/lib/cn";
import type { BlocklistEntry } from "../_data";
import { formatRelative } from "./ActiveProjectsPanel";

interface BlocklistTableProps {
  entries: readonly BlocklistEntry[];
  onUnblock?: (topicKey: string) => void;
  className?: string;
}

export function BlocklistTable({
  entries,
  onUnblock,
  className,
}: BlocklistTableProps) {
  if (entries.length === 0) {
    return (
      <div data-testid="memory-blocklist-empty" className={className}>
        <EmptyState
          title="블록리스트가 비어 있어요"
          description="거절한 토픽이 여기에 정렬됩니다. 영구 차단된 항목은 만료가 표시되지 않습니다."
        />
      </div>
    );
  }
  return (
    <div
      data-testid="memory-blocklist"
      className={cn(
        "overflow-hidden rounded-(--radius-l) border border-(--border) bg-(--card)",
        className,
      )}
    >
      <table className="w-full table-fixed border-collapse text-left">
        <colgroup>
          <col style={{ width: "30%" }} />
          <col style={{ width: "30%" }} />
          <col style={{ width: "20%" }} />
          <col style={{ width: "20%" }} />
        </colgroup>
        <thead className="border-b border-(--border) bg-(--surface) text-xs uppercase tracking-wide text-(--muted-foreground)">
          <tr>
            <th className="px-3 py-2 font-medium">토픽</th>
            <th className="px-3 py-2 font-medium">사유</th>
            <th className="px-3 py-2 font-medium">차단 시각 / 만료</th>
            <th className="px-3 py-2 text-right font-medium">액션</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr
              key={entry.topicKey}
              data-testid={`blocklist-${entry.topicKey}`}
              className="border-b border-(--border) text-sm last:border-b-0"
            >
              <td className="px-3 py-2 align-top">
                <span className="font-mono text-xs text-(--foreground-strong)">
                  {entry.topic}
                </span>
                <div className="mt-1 text-[11px] text-(--muted-foreground)">
                  key: {entry.topicKey}
                </div>
              </td>
              <td className="px-3 py-2 align-top text-(--foreground)">
                {entry.reason || (
                  <span className="text-(--muted-foreground)">사유 미기재</span>
                )}
              </td>
              <td className="px-3 py-2 align-top text-xs text-(--muted-foreground)">
                <div>차단: {formatRelative(entry.blockedAt)}</div>
                <div className="mt-0.5">
                  {entry.expiresAt ? (
                    <Badge tone="warning" size="sm">
                      만료 {formatExpiry(entry.expiresAt)}
                    </Badge>
                  ) : (
                    <Badge tone="danger" size="sm">
                      영구
                    </Badge>
                  )}
                </div>
              </td>
              <td className="px-3 py-2 align-top">
                <div className="flex justify-end">
                  {onUnblock ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => onUnblock(entry.topicKey)}
                      data-testid={`blocklist-${entry.topicKey}-unblock`}
                    >
                      차단 해제
                    </Button>
                  ) : null}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatExpiry(iso: string, now = Date.now()): string {
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return "—";
  const diffMs = ts - now;
  if (diffMs <= 0) return "곧 만료";
  const days = Math.ceil(diffMs / (1000 * 60 * 60 * 24));
  if (days < 1) return "오늘";
  return `${days}일 뒤`;
}
