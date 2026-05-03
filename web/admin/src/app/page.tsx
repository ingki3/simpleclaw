/**
 * 루트(`/`) — 대시보드로 redirect.
 * 본 Admin은 11개 영역이 모두 1:1 라우트를 갖는 구조이므로 루트는 단순 진입점 역할만 한다.
 */
import { redirect } from "next/navigation";

export default function RootIndex() {
  redirect("/dashboard");
}
