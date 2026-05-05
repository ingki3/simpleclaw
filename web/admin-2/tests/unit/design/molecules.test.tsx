/**
 * Molecular 컴포넌트 단위 테스트 — DESIGN.md §3.2 reusable 10종 박제 검증.
 */
import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import {
  AuditEntry,
  ConfirmGate,
  DryRunCard,
  EmptyState,
  FormRow,
  HealthDot,
  InputGroup,
  MaskedSecretRow,
  MetricCard,
  PolicyChip,
} from "@/design/molecules";

describe("Molecular — InputGroup", () => {
  it("hint 와 error 가 토글된다 (error 가 우선)", () => {
    const { rerender } = render(
      <InputGroup label="name" hint="도움말">
        <input />
      </InputGroup>,
    );
    expect(screen.getByText("도움말")).toBeDefined();
    rerender(
      <InputGroup label="name" hint="도움말" error="필수입니다">
        <input />
      </InputGroup>,
    );
    expect(screen.getByText("필수입니다")).toBeDefined();
    expect(screen.queryByText("도움말")).toBeNull();
  });
});

describe("Molecular — FormRow", () => {
  it("name / value 슬롯을 그린다", () => {
    render(<FormRow name="API key" value={<span>•••</span>} />);
    expect(screen.getByText("API key")).toBeDefined();
    expect(screen.getByText("•••")).toBeDefined();
  });
});

describe("Molecular — PolicyChip", () => {
  it.each(["hot", "service-restart", "process-restart"] as const)(
    "kind=%s",
    (kind) => {
      render(<PolicyChip kind={kind} />);
      expect(
        document.querySelector(`[data-policy="${kind}"]`),
      ).not.toBeNull();
    },
  );
});

describe("Molecular — DryRunCard", () => {
  it("apply / cancel 콜백이 동작한다", () => {
    const apply = vi.fn();
    const cancel = vi.fn();
    render(
      <DryRunCard
        before="60"
        after="30"
        impact="영향"
        onApply={apply}
        onCancel={cancel}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "변경 적용" }));
    fireEvent.click(screen.getByRole("button", { name: "취소" }));
    expect(apply).toHaveBeenCalledOnce();
    expect(cancel).toHaveBeenCalledOnce();
  });
});

describe("Molecular — AuditEntry", () => {
  it("outcome 별 라벨/태그를 그린다", () => {
    render(
      <AuditEntry
        actor="ingki3"
        action="config.update"
        target="llm.x"
        outcome="applied"
        traceId="01HW1ABCDEF"
        timestamp="23:30"
      />,
    );
    expect(screen.getByText("적용")).toBeDefined();
    expect(screen.getByText("ingki3")).toBeDefined();
    expect(screen.getByText("23:30")).toBeDefined();
  });
});

describe("Molecular — HealthDot", () => {
  it.each(["green", "amber", "red", "grey"] as const)("tone=%s", (tone) => {
    render(<HealthDot tone={tone} label={tone} />);
    expect(
      document.querySelector(`[data-tone="${tone}"]`),
    ).not.toBeNull();
  });
});

describe("Molecular — MetricCard", () => {
  it("delta number 의 부호로 tone 자동 결정", () => {
    const { rerender } = render(
      <MetricCard label="x" value="1" delta={3} />,
    );
    expect(screen.getByText("+3").className).toMatch(/color-success/);
    rerender(<MetricCard label="x" value="1" delta={-3} />);
    expect(screen.getByText("-3").className).toMatch(/color-error/);
  });
});

describe("Molecular — EmptyState", () => {
  it("title / description / action 을 모두 그린다", () => {
    render(
      <EmptyState
        title="비어있음"
        description="추가하세요"
        action={<button>추가</button>}
      />,
    );
    expect(screen.getByText("비어있음")).toBeDefined();
    expect(screen.getByText("추가하세요")).toBeDefined();
    expect(screen.getByRole("button", { name: "추가" })).toBeDefined();
  });
});

describe("Molecular — ConfirmGate", () => {
  it("키워드 미입력 시 비활성, 키워드 매칭 + 카운트다운 0 시 활성화", async () => {
    const onConfirm = vi.fn();
    // countdown=0: 키워드 일치 즉시 활성화 (타이머 의존 제거 — 안정 테스트).
    render(
      <ConfirmGate
        keyword="rotate"
        onConfirm={onConfirm}
        countdownSeconds={0}
      />,
    );
    const confirmBtn = screen.getByRole("button", {
      name: "실행",
    }) as HTMLButtonElement;
    expect(confirmBtn.disabled).toBe(true);

    await act(async () => {
      fireEvent.change(screen.getByLabelText("confirm keyword"), {
        target: { value: "wrong" },
      });
    });
    expect(confirmBtn.disabled).toBe(true);

    await act(async () => {
      fireEvent.change(screen.getByLabelText("confirm keyword"), {
        target: { value: "rotate" },
      });
    });
    expect(confirmBtn.disabled).toBe(false);
    fireEvent.click(confirmBtn);
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("카운트다운 동안에는 비활성, 만료 후 활성화", async () => {
    vi.useFakeTimers();
    render(<ConfirmGate keyword="x" onConfirm={() => {}} countdownSeconds={2} />);
    const confirmBtn = screen.getByRole("button", {
      name: "실행",
    }) as HTMLButtonElement;
    await act(async () => {
      fireEvent.change(screen.getByLabelText("confirm keyword"), {
        target: { value: "x" },
      });
    });
    // 1 tick — 아직 비활성.
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(confirmBtn.disabled).toBe(true);
    // 2 tick — 만료 후 활성.
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(confirmBtn.disabled).toBe(false);
    vi.useRealTimers();
  });
});

describe("Molecular — MaskedSecretRow", () => {
  it("키 이름과 마스킹 값을 함께 그린다", () => {
    render(
      <MaskedSecretRow
        keyName="keyring:claude_api_key"
        maskedPreview="••••1234"
        onCopy={() => {}}
      />,
    );
    expect(screen.getByText("keyring:claude_api_key")).toBeDefined();
    expect(screen.getByText("••••1234")).toBeDefined();
  });
});
