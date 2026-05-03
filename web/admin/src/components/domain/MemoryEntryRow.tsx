"use client";

/**
 * MemoryEntryRow — Memory 화면(BIZ-49)의 항목 1행.
 *
 * 1행 = MEMORY.md의 bullet 1개. 화면은 이 컴포넌트를 가상 스크롤 컨테이너 안에서 반복한다.
 *
 * 시각 토큰만 사용하며 인터랙션은 props 콜백으로 위임한다 — 행 자체는 상태를 갖지 않는다.
 *  - 편집은 인라인 textarea로 전환되고, 저장/취소 버튼이 노출된다.
 *  - 삭제 버튼은 `destructive-soft` 색(border-error/text-error)을 그대로 입는다.
 */

import { useEffect, useRef, useState } from "react";
import { Pencil, Trash2, Save, X } from "lucide-react";
import { Badge, type BadgeTone } from "@/components/atoms/Badge";
import { Button } from "@/components/atoms/Button";
import { cn } from "@/lib/cn";
import type { MemoryEntry, MemoryEntryType } from "@/lib/api/memory";

const TYPE_TONE: Record<MemoryEntryType, BadgeTone> = {
  user: "info",
  feedback: "warning",
  project: "brand",
  reference: "neutral",
};

const TYPE_LABEL: Record<MemoryEntryType, string> = {
  user: "user",
  feedback: "feedback",
  project: "project",
  reference: "reference",
};

export interface MemoryEntryRowProps {
  entry: MemoryEntry;
  /** 편집·삭제가 차단된 상태 — 드리밍 중일 때 true. */
  disabled?: boolean;
  onSave: (id: string, text: string) => void | Promise<void>;
  onRequestDelete: (entry: MemoryEntry) => void;
}

export function MemoryEntryRow({
  entry,
  disabled,
  onSave,
  onRequestDelete,
}: MemoryEntryRowProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(entry.text);
  const [saving, setSaving] = useState(false);
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  // 진입 시 textarea 자동 포커스 — 키보드 워크플로우 우선.
  useEffect(() => {
    if (editing && taRef.current) {
      taRef.current.focus();
      taRef.current.setSelectionRange(draft.length, draft.length);
    }
  }, [editing]); // eslint-disable-line react-hooks/exhaustive-deps

  // 외부에서 entry.text가 갱신되면(다른 항목 mutation으로 인한 인덱스 시프트 등) 동기화.
  useEffect(() => {
    if (!editing) setDraft(entry.text);
  }, [entry.text, editing]);

  const handleSave = async () => {
    if (saving || disabled) return;
    setSaving(true);
    try {
      await onSave(entry.id, draft);
      setEditing(false);
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    setDraft(entry.text);
    setEditing(false);
  };

  return (
    <li
      className={cn(
        "group flex items-start gap-3 border-b border-[--border-divider] px-3 py-2.5",
        "hover:bg-[--surface]",
      )}
    >
      <div className="flex w-24 shrink-0 flex-col items-start gap-1 pt-0.5">
        <span className="text-[10px] font-mono text-[--muted-foreground]">
          {entry.section}
        </span>
        {entry.type ? (
          <Badge tone={TYPE_TONE[entry.type]}>{TYPE_LABEL[entry.type]}</Badge>
        ) : (
          <Badge tone="neutral">—</Badge>
        )}
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        {editing ? (
          <textarea
            ref={taRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            spellCheck={false}
            rows={Math.max(2, Math.ceil(draft.length / 80))}
            className="w-full resize-y rounded-[--radius-m] border border-[--border] bg-[--card] p-2 text-sm leading-6 text-[--foreground] outline-none focus:border-[--primary]"
            aria-label={`항목 편집 — ${entry.id}`}
          />
        ) : (
          <p className="break-words text-sm leading-6 text-[--foreground]">
            {entry.text || (
              <span className="italic text-[--muted-foreground]">(빈 항목)</span>
            )}
          </p>
        )}
      </div>
      <div className="flex shrink-0 items-start gap-1.5 pt-0.5">
        {editing ? (
          <>
            <Button
              variant="primary"
              size="sm"
              onClick={handleSave}
              disabled={saving || disabled || draft === entry.text}
              leftIcon={<Save size={12} aria-hidden />}
            >
              {saving ? "저장 중…" : "저장"}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleCancel}
              disabled={saving}
              leftIcon={<X size={12} aria-hidden />}
            >
              취소
            </Button>
          </>
        ) : (
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setEditing(true)}
              disabled={disabled}
              leftIcon={<Pencil size={12} aria-hidden />}
              aria-label={`항목 편집 — ${entry.id}`}
            >
              편집
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onRequestDelete(entry)}
              disabled={disabled}
              leftIcon={<Trash2 size={12} aria-hidden />}
              aria-label={`항목 삭제 — ${entry.id}`}
              className="text-[--color-error] hover:bg-[--color-error-bg]"
            >
              삭제
            </Button>
          </>
        )}
      </div>
    </li>
  );
}
