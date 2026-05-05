/**
 * Atomic 컴포넌트 단위 테스트 — DESIGN.md §3.1 reusable 14종 박제 검증.
 *
 * 정책: 각 컴포넌트의 default + 핵심 변형 (variant/size/disabled/error) 가
 * 예외 없이 렌더되고, 토큰 클래스와 a11y attribute 가 누락되지 않는지 확인.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import {
  Badge,
  Button,
  Checkbox,
  Code,
  IconButton,
  Input,
  Label,
  Radio,
  SecretField,
  Select,
  StatusPill,
  Switch,
  Textarea,
  Tooltip,
} from "@/design/atoms";

describe("Atomic — Button", () => {
  it("default 가 primary variant 로 렌더된다", () => {
    render(<Button>Save</Button>);
    const btn = screen.getByRole("button", { name: "Save" });
    expect(btn.dataset.variant).toBe("primary");
    expect(btn.dataset.size).toBe("md");
  });

  it.each(["primary", "secondary", "outline", "ghost", "destructive"] as const)(
    "variant=%s 로 렌더된다",
    (variant) => {
      render(<Button variant={variant}>{variant}</Button>);
      expect(
        screen.getByRole("button", { name: variant }).dataset.variant,
      ).toBe(variant);
    },
  );

  it("disabled 시 cursor-not-allowed 클래스를 갖는다", () => {
    render(<Button disabled>Disabled</Button>);
    const btn = screen.getByRole("button", {
      name: "Disabled",
    }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.className).toMatch(/cursor-not-allowed/);
  });
});

describe("Atomic — IconButton", () => {
  it("aria-label 과 icon 슬롯을 그린다", () => {
    render(<IconButton aria-label="search" icon={<span>🔍</span>} />);
    expect(screen.getByRole("button", { name: "search" })).toBeDefined();
  });
});

describe("Atomic — Input", () => {
  it("error prop 이 data-error 와 클래스에 반영된다", () => {
    render(<Input placeholder="email" error data-testid="x" />);
    const wrap = screen.getByTestId("x").parentElement;
    expect(wrap?.dataset.error).toBe("true");
  });
});

describe("Atomic — Textarea", () => {
  it("기본/disabled 상태가 렌더된다", () => {
    render(<Textarea defaultValue="hello" disabled />);
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).disabled).toBe(
      true,
    );
  });
});

describe("Atomic — Select", () => {
  it("options 가 모두 렌더된다", () => {
    render(
      <Select
        options={[
          { value: "a", label: "Alpha" },
          { value: "b", label: "Beta" },
        ]}
      />,
    );
    expect(screen.getByRole("option", { name: "Alpha" })).toBeDefined();
    expect(screen.getByRole("option", { name: "Beta" })).toBeDefined();
  });
});

describe("Atomic — Switch", () => {
  it("role=switch + aria-checked 가 noted 된다", () => {
    const fn = vi.fn();
    render(<Switch checked={false} onCheckedChange={fn} label="auto" />);
    const sw = screen.getByRole("switch", { name: "auto" });
    expect(sw.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(sw);
    expect(fn).toHaveBeenCalledWith(true);
  });
});

describe("Atomic — Checkbox / Radio", () => {
  it("Checkbox 가 라벨과 함께 렌더된다", () => {
    render(<Checkbox label="agree" defaultChecked />);
    expect(screen.getByLabelText("agree")).toBeDefined();
  });

  it("Radio 가 group 내에서 렌더된다", () => {
    render(<Radio label="A" name="g" value="a" defaultChecked />);
    expect(screen.getByLabelText("A")).toBeDefined();
  });
});

describe("Atomic — Label", () => {
  it("required 마커를 그린다", () => {
    render(<Label required>name</Label>);
    expect(screen.getByText("name")).toBeDefined();
    expect(screen.getByText("*")).toBeDefined();
  });

  it("optional 마커를 그린다", () => {
    render(<Label optional>field</Label>);
    expect(screen.getByText("(선택)")).toBeDefined();
  });
});

describe("Atomic — Badge / StatusPill", () => {
  it.each(["neutral", "success", "warning", "danger", "info", "brand"] as const)(
    "Badge tone=%s",
    (tone) => {
      render(<Badge tone={tone}>{tone}</Badge>);
      expect(screen.getByText(tone).dataset.tone).toBe(tone);
    },
  );

  it.each(["success", "warning", "error", "info", "neutral"] as const)(
    "StatusPill tone=%s",
    (tone) => {
      const { container } = render(
        <StatusPill tone={tone}>label-{tone}</StatusPill>,
      );
      // outer span 이 data-tone 을 갖는다.
      const outer = container.querySelector("[data-tone]");
      expect(outer?.getAttribute("data-tone")).toBe(tone);
      expect(outer?.textContent).toContain(`label-${tone}`);
    },
  );
});

describe("Atomic — SecretField", () => {
  it("기본은 마스킹값을 보여주고, reveal 시 실제값으로 바뀐다", () => {
    render(
      <SecretField
        maskedPreview="••••1234"
        revealedValue="real-secret"
        onReveal={() => {}}
      />,
    );
    expect(screen.getByText("••••1234")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "보기" }));
    expect(screen.getByText("real-secret")).toBeDefined();
  });
});

describe("Atomic — Code", () => {
  it("inline 과 block 모드 모두 렌더된다", () => {
    const { rerender } = render(<Code>foo</Code>);
    expect(screen.getByText("foo").tagName.toLowerCase()).toBe("code");
    rerender(<Code block>bar</Code>);
    expect(screen.getByText("bar").parentElement?.tagName.toLowerCase()).toBe(
      "pre",
    );
  });
});

describe("Atomic — Tooltip", () => {
  it("트리거를 그리고 role=tooltip 노드를 함께 noted 한다", () => {
    render(
      <Tooltip content="tip">
        <button>trg</button>
      </Tooltip>,
    );
    expect(screen.getByRole("button", { name: "trg" })).toBeDefined();
    expect(screen.getByRole("tooltip", { hidden: true })).toBeDefined();
  });
});
