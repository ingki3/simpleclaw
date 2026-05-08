/**
 * SecurityPolicyCard — admin.pen `BIurh` (cardSecurity) 박제.
 *
 * 4 행 메타 + secondary "정책 편집" 버튼 (Secrets 영역으로의 진입점).
 * 본 단계는 정책 편집 흐름이 별도 sub-issue 이므로 onClick 은 부모로 위임.
 */
"use client";

import type { ReactNode } from "react";
import { Badge } from "@/design/atoms/Badge";
import { Button } from "@/design/atoms/Button";
import { cn } from "@/lib/cn";
import type { SecurityPolicyInfo } from "../_data";

interface SecurityPolicyCardProps {
  info: SecurityPolicyInfo;
  /** "정책 편집" 클릭 — 부모가 Secrets 영역으로 이동 등 결정. */
  onEdit?: () => void;
  className?: string;
}

export function SecurityPolicyCard({
  info,
  onEdit,
  className,
}: SecurityPolicyCardProps) {
  return (
    <section
      data-testid="security-policy-card"
      aria-label="Security Policy"
      className={cn(
        "flex flex-col gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-5 shadow-(--shadow-sm)",
        className,
      )}
    >
      <h2 className="text-sm font-semibold text-(--foreground-strong)">
        Security Policy
      </h2>
      <Row
        label="인증 모드"
        value={<Badge tone="brand">{info.authMode}</Badge>}
      />
      <Row label="RBAC" value={info.rbacRoles} />
      <Row label="Audit 보존" value={`${info.auditRetentionDays} 일`} />
      <Row label="Secret 회전 주기" value={`${info.secretRotationDays} 일`} />
      <Button
        variant="secondary"
        size="sm"
        onClick={onEdit}
        data-testid="security-policy-edit"
      >
        정책 편집
      </Button>
    </section>
  );
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 text-xs">
      <span className="text-(--muted-foreground)">{label}</span>
      {typeof value === "string" ? (
        <span className="font-mono text-(--foreground)">{value}</span>
      ) : (
        value
      )}
    </div>
  );
}
