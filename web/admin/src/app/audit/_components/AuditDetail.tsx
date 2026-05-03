"use client";

/**
 * AuditDetail — Drawer 본문에 띄우는 단일 감사 항목 상세.
 *
 * 구성:
 *  1. 메타: 액션·영역·target·actor·trace_id·결과 pill·재시작 필요 여부.
 *  2. before / after diff — 두 컬럼 코드 블록(JSON pretty). 좁은 화면은 세로로 스택.
 *  3. Undo 패널 — 5분 윈도 안에서만 활성화. 윈도 밖이면 사유 표시.
 *
 * Undo 흐름:
 *  - 버튼 클릭 시 부모가 ConfirmGate를 띄운다 — 본 컴포넌트는 *액션을 알리는 콜백*만 노출.
 *  - 부모가 백엔드 호출 결과를 토스트로 표시하고 목록을 갱신한다.
 *
 * 시크릿 노출 정책:
 *  - 본 컴포넌트는 어떠한 마스킹도 하지 않는다. 백엔드가 ``_mask_secrets``로
 *    이미 ``••••<last4>`` 또는 시크릿 참조 문자열로 변환한 값을 그대로 표시한다.
 *  - 시크릿 키 옆 string 값이 평문으로 보이는 것은 *버그* — 백엔드 마스킹을 점검해야 한다.
 */

import { useEffect, useState } from "react";
import { History, Undo2 } from "lucide-react";
import { Badge } from "@/components/atoms/Badge";
import { Button } from "@/components/atoms/Button";
import { StatusPill } from "@/components/atoms/StatusPill";
import {
  formatAbsoluteTs,
  formatPayload,
  isUndoableNow,
  outcomeTone,
  remainingWindowLabel,
  UNDO_WINDOW_MS,
  type AuditEntryDTO,
} from "./audit-utils";

interface AuditDetailProps {
  entry: AuditEntryDTO;
  /** Undo 진행 중이면 true — 버튼 비활성화 + 라벨 변경. */
  isUndoing: boolean;
  /** Undo 트리거 — 부모가 ConfirmGate 후 백엔드 호출을 책임진다. */
  onUndoRequest: () => void;
  /** 트레이스 보기 — 부모가 /logs로 라우팅. */
  onViewTrace?: (traceId: string) => void;
}

export function AuditDetail({
  entry,
  isUndoing,
  onUndoRequest,
  onViewTrace,
}: AuditDetailProps) {
  // 5분 윈도 카운트다운을 1초 간격으로 갱신해 버튼 활성화/비활성을 실시간 반영.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const undoable = isUndoableNow(entry, now);
  const remain = remainingWindowLabel(entry, now);
  const tone = outcomeTone(entry.outcome);

  return (
    <div className="flex flex-col gap-5">
      <section
        aria-label="메타"
        className="flex flex-col gap-2 rounded-(--radius-m) border border-(--border) bg-(--surface) p-3"
      >
        <div className="flex flex-wrap items-center gap-2">
          <History size={14} aria-hidden className="text-(--muted-foreground)" />
          <span className="font-medium text-(--foreground-strong)">
            {entry.action}
          </span>
          <Badge tone="neutral">{entry.area || "—"}</Badge>
          <StatusPill tone={tone} className="ml-auto">
            {entry.outcome}
          </StatusPill>
        </div>
        <code className="break-all font-mono text-xs text-(--muted-foreground)">
          {entry.target || "(target 없음)"}
        </code>
        <dl className="mt-2 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-xs text-(--muted-foreground)">
          <dt>시각</dt>
          <dd className="text-(--foreground)">{formatAbsoluteTs(entry.ts)}</dd>
          <dt>actor</dt>
          <dd className="text-(--foreground)">{entry.actor_id || "system"}</dd>
          {entry.trace_id ? (
            <>
              <dt>trace_id</dt>
              <dd className="flex items-center gap-2 text-(--foreground)">
                <code className="font-mono">{entry.trace_id}</code>
                {onViewTrace ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => onViewTrace(entry.trace_id)}
                  >
                    /logs에서 보기
                  </Button>
                ) : null}
              </dd>
            </>
          ) : null}
          {entry.affected_modules.length > 0 ? (
            <>
              <dt>영향 모듈</dt>
              <dd className="text-(--foreground)">
                {entry.affected_modules.join(", ")}
              </dd>
            </>
          ) : null}
          {entry.requires_restart ? (
            <>
              <dt>재시작</dt>
              <dd>
                <Badge tone="warning">필요</Badge>
              </dd>
            </>
          ) : null}
          {entry.reason ? (
            <>
              <dt>사유</dt>
              <dd className="text-(--foreground)">{entry.reason}</dd>
            </>
          ) : null}
        </dl>
      </section>

      <section
        aria-label="변경 내용"
        className="flex flex-col gap-2"
      >
        <header className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-(--foreground-strong)">
            변경 내용 (before → after)
          </h3>
          <span className="text-xs text-(--muted-foreground)">
            시크릿 값은 ``••••&lt;last4&gt;``로 마스킹됨
          </span>
        </header>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <DiffPane label="before" tone="neutral" payload={entry.before} />
          <DiffPane label="after" tone="success" payload={entry.after} />
        </div>
      </section>

      <section
        aria-label="되돌리기"
        className="flex flex-col gap-2 rounded-(--radius-m) border border-(--border) bg-(--surface) p-3"
      >
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-(--foreground-strong)">
            되돌리기
          </h3>
          <span className="text-xs text-(--muted-foreground)">
            5분 윈도 — 남은 시간 <code className="font-mono">{remain}</code>
          </span>
        </div>
        <p className="text-xs text-(--muted-foreground)">
          {undoUnavailableReason(entry, now) ??
            "버튼을 누르면 확인 후 백엔드에 되돌리기를 요청합니다. 새 감사 항목이 생성됩니다."}
        </p>
        <div>
          <Button
            variant="secondary"
            size="sm"
            leftIcon={<Undo2 size={14} aria-hidden />}
            disabled={!undoable || isUndoing}
            onClick={onUndoRequest}
            aria-label="이 변경 되돌리기"
          >
            {isUndoing ? "되돌리는 중…" : "되돌리기"}
          </Button>
        </div>
      </section>
    </div>
  );
}

/**
 * 비활성화 사유를 운영자 언어로 한 문장 — 비활성화 케이스가 아니면 ``null``.
 */
function undoUnavailableReason(
  entry: Pick<AuditEntryDTO, "undoable" | "outcome" | "ts">,
  now: number,
): string | null {
  if (!entry.undoable) {
    return "이 변경은 백엔드가 되돌릴 수 없는 액션으로 표시했어요 (예: 시크릿 회전, 시스템 재시작).";
  }
  if (entry.outcome !== "applied" && entry.outcome !== "pending") {
    return `결과가 '${entry.outcome}'인 항목은 되돌릴 수 없어요.`;
  }
  if (!isUndoableNow(entry, now)) {
    const minutes = Math.floor(UNDO_WINDOW_MS / 60_000);
    return `기록 시각으로부터 ${minutes}분이 지나 윈도가 만료됐어요.`;
  }
  return null;
}

interface DiffPaneProps {
  label: string;
  tone: "neutral" | "success";
  payload: unknown;
}

function DiffPane({ label, tone, payload }: DiffPaneProps) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <Badge tone={tone === "success" ? "success" : "neutral"}>{label}</Badge>
      </div>
      <pre className="overflow-auto whitespace-pre-wrap break-all rounded-(--radius-m) border border-(--border) bg-(--card) p-3 text-xs leading-relaxed text-(--foreground)">
        <code className="font-mono">{formatPayload(payload)}</code>
      </pre>
    </div>
  );
}
