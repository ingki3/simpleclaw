/**
 * AddSecretModal 단위 테스트 — 폼 검증 / 정책 선택 / 평문 누출 금지.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import {
  AddSecretModal,
  maskValue,
  validate,
} from "@/app/(shell)/secrets/_components/AddSecretModal";

const SCOPES = ["llm-provider", "channel", "system", "service"] as const;

describe("AddSecretModal", () => {
  it("open=false 면 모달이 렌더되지 않는다", () => {
    render(
      <AddSecretModal
        open={false}
        onClose={() => {}}
        scopes={SCOPES}
        existingKeyNames={[]}
        onSubmit={() => {}}
      />,
    );
    expect(screen.queryByTestId("add-secret-modal")).toBeNull();
  });

  it("open 시 키 이름 input 이 autofocus", () => {
    render(
      <AddSecretModal
        open
        onClose={() => {}}
        scopes={SCOPES}
        existingKeyNames={[]}
        onSubmit={() => {}}
      />,
    );
    const input = screen.getByTestId("add-secret-key-name") as HTMLInputElement;
    expect(document.activeElement).toBe(input);
  });

  it("값이 비어있으면 검증 실패 + onSubmit 미호출", () => {
    const onSubmit = vi.fn();
    render(
      <AddSecretModal
        open
        onClose={() => {}}
        scopes={SCOPES}
        existingKeyNames={[]}
        onSubmit={onSubmit}
      />,
    );
    fireEvent.click(screen.getByTestId("add-secret-submit"));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId("add-secret-key-name-error")).toBeDefined();
    expect(screen.getByTestId("add-secret-value-error")).toBeDefined();
  });

  it("키 이름이 패턴에 안 맞으면 검증 실패", () => {
    const onSubmit = vi.fn();
    render(
      <AddSecretModal
        open
        onClose={() => {}}
        scopes={SCOPES}
        existingKeyNames={[]}
        onSubmit={onSubmit}
      />,
    );
    fireEvent.change(screen.getByTestId("add-secret-key-name"), {
      target: { value: "Bad Name!" },
    });
    fireEvent.change(screen.getByTestId("add-secret-value"), {
      target: { value: "sk-secret-value" },
    });
    fireEvent.click(screen.getByTestId("add-secret-submit"));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId("add-secret-key-name-error").textContent).toMatch(
      /소문자/,
    );
  });

  it("기존 키 이름과 중복이면 검증 실패", () => {
    const onSubmit = vi.fn();
    render(
      <AddSecretModal
        open
        onClose={() => {}}
        scopes={SCOPES}
        existingKeyNames={["llm.anthropic_api_key"]}
        onSubmit={onSubmit}
      />,
    );
    fireEvent.change(screen.getByTestId("add-secret-key-name"), {
      target: { value: "llm.anthropic_api_key" },
    });
    fireEvent.change(screen.getByTestId("add-secret-value"), {
      target: { value: "sk-test" },
    });
    fireEvent.click(screen.getByTestId("add-secret-submit"));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId("add-secret-key-name-error").textContent).toMatch(
      /이미/,
    );
  });

  it("정상 입력 → onSubmit 에 trimmed 값 전달, 평문은 그대로 전달되지만 호출자 책임", () => {
    const onSubmit = vi.fn();
    const onClose = vi.fn();
    render(
      <AddSecretModal
        open
        onClose={onClose}
        scopes={SCOPES}
        existingKeyNames={[]}
        onSubmit={onSubmit}
      />,
    );
    fireEvent.change(screen.getByTestId("add-secret-key-name"), {
      target: { value: "  channel.slack_token  " },
    });
    fireEvent.change(screen.getByTestId("add-secret-scope"), {
      target: { value: "channel" },
    });
    fireEvent.change(screen.getByTestId("add-secret-value"), {
      target: { value: "xoxb-1234567890" },
    });
    fireEvent.change(screen.getByTestId("add-secret-note"), {
      target: { value: "  Slack 봇 토큰  " },
    });
    // 정책 변경 — service-restart 라디오 선택.
    const radio = screen.getByTestId(
      "add-secret-policy-service-restart",
    ).querySelector("input") as HTMLInputElement;
    fireEvent.click(radio);

    fireEvent.click(screen.getByTestId("add-secret-submit"));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith({
      keyName: "channel.slack_token",
      scope: "channel",
      value: "xoxb-1234567890",
      policy: "service-restart",
      note: "Slack 봇 토큰",
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("기본 reveal 토글 — type 가 password ↔ text 로 전환된다", () => {
    render(
      <AddSecretModal
        open
        onClose={() => {}}
        scopes={SCOPES}
        existingKeyNames={[]}
        onSubmit={() => {}}
      />,
    );
    const value = screen.getByTestId("add-secret-value") as HTMLInputElement;
    expect(value.type).toBe("password");
    fireEvent.click(screen.getByTestId("add-secret-reveal"));
    expect(value.type).toBe("text");
    fireEvent.click(screen.getByTestId("add-secret-reveal"));
    expect(value.type).toBe("password");
  });

  it("취소 버튼 → onClose", () => {
    const onClose = vi.fn();
    render(
      <AddSecretModal
        open
        onClose={onClose}
        scopes={SCOPES}
        existingKeyNames={[]}
        onSubmit={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("add-secret-cancel"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("validate(add-secret)", () => {
  it("정상 입력은 빈 객체 반환", () => {
    expect(
      Object.keys(
        validate(
          { keyName: "llm.test_key", value: "sk-abcdef" },
          [],
        ),
      ),
    ).toHaveLength(0);
  });

  it("키 이름이 짧으면 실패", () => {
    expect(
      validate({ keyName: "ab", value: "sk-abcdef" }, []).keyName,
    ).toBeDefined();
  });

  it("대문자가 있으면 실패", () => {
    expect(
      validate({ keyName: "LLM.api_key", value: "sk-abcdef" }, []).keyName,
    ).toBeDefined();
  });

  it("값이 빈 문자열이면 실패", () => {
    expect(
      validate({ keyName: "llm.test_key", value: "" }, []).value,
    ).toBeDefined();
  });
});

describe("maskValue", () => {
  it("마지막 4자리만 표시", () => {
    expect(maskValue("sk-1234567890")).toBe("••••7890");
  });

  it("4자 미만이어도 padding 으로 형식 유지", () => {
    expect(maskValue("ab")).toBe("••••ab");
  });

  it("빈 문자열은 0000 fallback", () => {
    expect(maskValue("")).toBe("••••0000");
  });
});
