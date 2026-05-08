"use client";

/**
 * TelegramCard — admin.pen `weuuW` 상단 Telegram 봇 카드.
 *
 * 구성 (위 → 아래):
 *  1) 헤더 — h2 "Telegram" + 우측 StatusPill (연결 상태)
 *  2) Bot Token — 마스킹 표시 (Code) + secret URI + "회전" 버튼
 *  3) Allowlist (chat IDs) — Input + 보조 카운트
 *  4) 푸터 — "테스트 메시지" / "저장" 버튼
 *
 * Switch / Input mutation 은 부모가 prop 으로 주입한 콜백으로 박제 — 실제
 * mutation 은 데몬 통합 단계.
 */

import { Button } from "@/design/atoms/Button";
import { Input } from "@/design/atoms/Input";
import { Label } from "@/design/atoms/Label";
import { StatusPill } from "@/design/atoms/StatusPill";
import { cn } from "@/lib/cn";
import type { TelegramChannel } from "../_data";

interface TelegramCardProps {
  channel: TelegramChannel;
  /** Allowlist 입력 값 — 부모가 controlled 로 보유. */
  allowlistInput: string;
  onAllowlistChange: (next: string) => void;
  /** "회전" 버튼 — Token Rotate ConfirmGate modal 트리거. */
  onRotateToken: () => void;
  /** "테스트 메시지" 버튼 — 부모가 mock console 로 박제. */
  onSendTest: () => void;
  /** "저장" 버튼 — Allowlist 변경 사항 commit (부모 박제). */
  onSave: () => void;
  className?: string;
}

export function TelegramCard({
  channel,
  allowlistInput,
  onAllowlistChange,
  onRotateToken,
  onSendTest,
  onSave,
  className,
}: TelegramCardProps) {
  // chat ID 개수는 ", " 분리 후 빈 문자열 제외 — 표시 카운트의 SSOT.
  const chatCount = allowlistInput
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0).length;

  return (
    <section
      data-testid="telegram-card"
      data-status={channel.status}
      className={cn(
        "flex flex-col gap-4 rounded-(--radius-l) border border-(--border) bg-(--card) p-6",
        className,
      )}
    >
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-(--foreground-strong)">
          Telegram
        </h2>
        <StatusPill tone={channel.statusTone} className="shrink-0">
          {channel.statusLabel}
        </StatusPill>
      </header>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="telegram-bot-token">Bot Token</Label>
        <div
          data-testid="telegram-bot-token"
          className="flex flex-wrap items-center gap-3 rounded-(--radius-m) border border-(--border-strong) bg-(--card) px-3 py-2 text-sm"
        >
          <span className="font-mono text-(--foreground)">
            {channel.tokenMasked}
          </span>
          <span className="font-mono text-xs text-(--muted-foreground)">
            ({channel.tokenSecretUri})
          </span>
          <Button
            size="sm"
            variant="ghost"
            onClick={onRotateToken}
            className="ml-auto"
            data-testid="telegram-bot-token-rotate"
          >
            회전
          </Button>
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="telegram-allowlist">Allowlist (chat IDs)</Label>
        <Input
          id="telegram-allowlist"
          value={allowlistInput}
          onChange={(e) => onAllowlistChange(e.currentTarget.value)}
          placeholder="123456789, 987654321"
          data-testid="telegram-allowlist"
          trailing={
            <span className="font-mono text-xs">
              {chatCount} chat
            </span>
          }
        />
      </div>

      <footer className="flex flex-wrap items-center justify-end gap-2">
        <Button
          variant="secondary"
          onClick={onSendTest}
          data-testid="telegram-test"
        >
          테스트 메시지
        </Button>
        <Button
          variant="primary"
          onClick={onSave}
          data-testid="telegram-save"
        >
          저장
        </Button>
      </footer>
    </section>
  );
}
