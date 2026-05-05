/**
 * WebhookEditModal 단위 테스트 — prefill / 검증 / 저장 / 시뮬레이션 트리거.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import {
  WebhookEditModal,
  validate,
} from "@/app/(shell)/channels/_components/WebhookEditModal";
import { ENDPOINT } from "./_fixture";

describe("WebhookEditModal", () => {
  it("endpoint=null 이면 modal 이 렌더되지 않는다", () => {
    render(
      <WebhookEditModal
        open
        endpoint={null}
        onClose={() => {}}
        onSubmit={() => {}}
        onOpenSimulation={() => {}}
      />,
    );
    expect(screen.queryByTestId("webhook-edit-modal")).toBeNull();
  });

  it("endpoint 가 있으면 prefill 후 헤더에 id 노출", () => {
    render(
      <WebhookEditModal
        open
        endpoint={ENDPOINT}
        onClose={() => {}}
        onSubmit={() => {}}
        onOpenSimulation={() => {}}
      />,
    );
    expect(screen.getByTestId("webhook-edit-modal").textContent).toContain(
      ENDPOINT.id,
    );
    expect(
      (screen.getByTestId("webhook-edit-url") as HTMLInputElement).value,
    ).toBe(ENDPOINT.url);
    expect(
      (screen.getByTestId("webhook-edit-secret") as HTMLInputElement).value,
    ).toBe(ENDPOINT.secretEnv);
    expect(
      (screen.getByTestId("webhook-edit-rate-limit") as HTMLInputElement).value,
    ).toBe(String(ENDPOINT.rateLimitPerSec));
  });

  it("저장 버튼은 onSubmit(id, draft) + onClose 호출", () => {
    const onSubmit = vi.fn();
    const onClose = vi.fn();
    render(
      <WebhookEditModal
        open
        endpoint={ENDPOINT}
        onClose={onClose}
        onSubmit={onSubmit}
        onOpenSimulation={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("webhook-edit-submit"));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0]?.[0]).toBe(ENDPOINT.id);
    expect(onSubmit.mock.calls[0]?.[1]).toMatchObject({
      url: ENDPOINT.url,
      secretEnv: ENDPOINT.secretEnv,
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("URL 비우면 검증 실패 + onSubmit 미호출", () => {
    const onSubmit = vi.fn();
    render(
      <WebhookEditModal
        open
        endpoint={ENDPOINT}
        onClose={() => {}}
        onSubmit={onSubmit}
        onOpenSimulation={() => {}}
      />,
    );
    fireEvent.change(screen.getByTestId("webhook-edit-url"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByTestId("webhook-edit-submit"));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId("webhook-edit-url-error")).toBeDefined();
  });

  it("'트래픽 시뮬레이션' 버튼은 onOpenSimulation(draft) 호출", () => {
    const onOpenSimulation = vi.fn();
    render(
      <WebhookEditModal
        open
        endpoint={ENDPOINT}
        onClose={() => {}}
        onSubmit={() => {}}
        onOpenSimulation={onOpenSimulation}
      />,
    );
    fireEvent.click(screen.getByTestId("webhook-edit-simulate"));
    expect(onOpenSimulation).toHaveBeenCalledTimes(1);
    expect(onOpenSimulation.mock.calls[0]?.[0]).toMatchObject({
      id: ENDPOINT.id,
      url: ENDPOINT.url,
    });
  });

  it("취소 버튼은 onClose 만 호출", () => {
    const onClose = vi.fn();
    const onSubmit = vi.fn();
    render(
      <WebhookEditModal
        open
        endpoint={ENDPOINT}
        onClose={onClose}
        onSubmit={onSubmit}
        onOpenSimulation={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("webhook-edit-cancel"));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
  });
});

describe("validate(webhook-edit)", () => {
  it("정상 endpoint 는 빈 객체", () => {
    expect(Object.keys(validate(ENDPOINT))).toHaveLength(0);
  });

  it("URL 이 http(s) 미시작 시 실패", () => {
    const errs = validate({ ...ENDPOINT, url: "ftp://x.dev" });
    expect(errs.url).toBeDefined();
  });

  it("secretEnv 비빈 시 실패", () => {
    const errs = validate({ ...ENDPOINT, secretEnv: "  " });
    expect(errs.secretEnv).toBeDefined();
  });

  it("rateLimitPerSec 음수 시 실패", () => {
    const errs = validate({ ...ENDPOINT, rateLimitPerSec: -1 });
    expect(errs.rateLimitPerSec).toBeDefined();
  });

  it("concurrency 0 시 실패 (1 이상 정수 요구)", () => {
    const errs = validate({ ...ENDPOINT, concurrency: 0 });
    expect(errs.concurrency).toBeDefined();
  });

  it("concurrency 소수 시 실패", () => {
    const errs = validate({ ...ENDPOINT, concurrency: 1.5 });
    expect(errs.concurrency).toBeDefined();
  });

  it("bodySchema 빈 문자열 시 실패", () => {
    const errs = validate({ ...ENDPOINT, bodySchema: "" });
    expect(errs.bodySchema).toBeDefined();
  });
});
