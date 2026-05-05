/**
 * (shell) — Admin 2.0 의 영역 라우트 그룹 (BIZ-113).
 *
 * 본 그룹의 모든 라우트는 좌측 Sidebar + 상단 Topbar + ⌘K Command Palette 의
 * 통일된 셸 안에서 렌더된다. 영역별 콘텐츠는 S3~S13 sub-issue 가 채운다.
 */
import type { ReactNode } from "react";
import { Shell } from "./_components/Shell";

export default function ShellLayout({ children }: { children: ReactNode }) {
  return <Shell>{children}</Shell>;
}
