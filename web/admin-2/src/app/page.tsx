/**
 * 루트(`/`) — Admin 2.0 진입 시 영역 셸의 기본 화면(`/dashboard`)으로 리다이렉트.
 *
 * S2 (BIZ-113) 부터는 `/` 자체가 별도 콘텐츠를 갖지 않는다 — 11개 영역 라우트가
 * 단일 진실이고, 그 중 dashboard 가 운영자의 기본 진입점이다.
 */
import { redirect } from "next/navigation";

export default function HomePage() {
  redirect("/dashboard");
}
