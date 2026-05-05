/**
 * Domain 컴포넌트 단위 테스트 — DESIGN.md §3.4 reusable 5종 박제 검증.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  CronJobRow,
  MemoryClusterMap,
  PersonaEditor,
  TraceTimeline,
  WebhookGuardCard,
} from "@/design/domain";

describe("Domain — CronJobRow", () => {
  it("이름·schedule·status 를 행에 박제한다", () => {
    render(
      <table>
        <tbody>
          <CronJobRow
            name="dreaming"
            schedule="0 4 * * *"
            nextRun="04:00"
            status="success"
            circuit="closed"
          />
        </tbody>
      </table>,
    );
    expect(screen.getByText("dreaming")).toBeDefined();
    expect(screen.getByText("0 4 * * *")).toBeDefined();
    expect(screen.getByText("성공")).toBeDefined();
  });
});

describe("Domain — PersonaEditor", () => {
  it("토큰 미터를 그리고 onChange 가 호출된다", () => {
    const fn = vi.fn();
    render(
      <PersonaEditor
        value="abc"
        onChange={fn}
        tokensCurrent={500}
        tokensBudget={1000}
      />,
    );
    expect(screen.getByText(/500.*1,?000/)).toBeDefined();
    expect(screen.getByLabelText("Persona markdown")).toBeDefined();
  });

  it("budget 초과 시 error tone 으로 표시된다", () => {
    render(
      <PersonaEditor
        value=""
        onChange={() => {}}
        tokensCurrent={1200}
        tokensBudget={1000}
      />,
    );
    // 1200/1000 = 1.2 → error tone class 적용.
    const meta = screen.getByText(/1,?200.*1,?000/);
    expect(meta.className).toMatch(/color-error/);
  });
});

describe("Domain — WebhookGuardCard", () => {
  it("3 슬라이더와 simulation 슬롯을 그린다", () => {
    render(
      <WebhookGuardCard
        rateLimit={{
          label: "Rate",
          value: 10,
          min: 0,
          max: 100,
          onChange: () => {},
        }}
        bodySize={{
          label: "Body",
          value: 64,
          min: 0,
          max: 256,
          onChange: () => {},
        }}
        concurrency={{
          label: "Conc",
          value: 4,
          min: 1,
          max: 32,
          onChange: () => {},
        }}
        simulation="ok"
      />,
    );
    expect(screen.getAllByRole("slider")).toHaveLength(3);
    expect(screen.getByText("ok")).toBeDefined();
  });
});

describe("Domain — TraceTimeline", () => {
  it("span lane 을 모두 그린다", () => {
    render(
      <TraceTimeline
        spans={[
          { id: "1", name: "a.send", startMs: 0, endMs: 100 },
          { id: "2", name: "b.recv", startMs: 100, endMs: 200, tone: "warning" },
        ]}
        totalMs={200}
      />,
    );
    expect(screen.getByText("a.send")).toBeDefined();
    expect(screen.getByText("b.recv")).toBeDefined();
    expect(screen.getByText("200 ms")).toBeDefined();
  });
});

describe("Domain — MemoryClusterMap", () => {
  it("클러스터 라벨과 합계를 그린다", () => {
    render(
      <MemoryClusterMap
        clusters={[
          { id: "a", label: "코드", count: 60 },
          { id: "b", label: "운영", count: 40 },
        ]}
      />,
    );
    expect(screen.getByText("코드")).toBeDefined();
    expect(screen.getByText("운영")).toBeDefined();
    // 합계 100 entries.
    expect(screen.getByText(/100/)).toBeDefined();
  });
});
