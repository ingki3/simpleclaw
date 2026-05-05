/**
 * 스모크 단위 테스트 — Admin 2.0 루트 페이지 (BIZ-113 부터는 redirect).
 *
 * S2 부터 `/` 는 영역 셸의 기본 화면(`/dashboard`)으로 즉시 리다이렉트한다.
 * Next 의 `redirect()` 는 내부적으로 NEXT_REDIRECT 라는 이름의 에러를 throw 하므로,
 * 본 테스트는 그 호출이 실제로 일어나는지를 검증한다.
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  redirect: vi.fn((path: string) => {
    throw new Error(`NEXT_REDIRECT:${path}`);
  }),
}));

import HomePage from "@/app/page";
import { redirect } from "next/navigation";

describe("Admin 2.0 루트 페이지", () => {
  it("/dashboard 로 리다이렉트한다", () => {
    expect(() => HomePage()).toThrow(/NEXT_REDIRECT:\/dashboard/);
    expect(redirect).toHaveBeenCalledWith("/dashboard");
  });
});
