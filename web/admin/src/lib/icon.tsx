/**
 * lucide-react 아이콘을 이름 문자열로 lazy-import 없이 가져오기 위한 얇은 어댑터.
 *
 * 디자인 토큰 측에서 아이콘은 "이름"으로만 참조되므로(예: nav.ts의 `icon: "Brain"`),
 * 컴포넌트가 그 문자열을 받아 lucide의 React 컴포넌트로 변환할 한 곳이 필요하다.
 * 모든 사용처가 동일 모듈에서 import하면 트리셰이킹 손실이 거의 없다.
 */

import { type LucideIcon, icons } from "lucide-react";

export function getIcon(name: string): LucideIcon {
  const Icon = (icons as Record<string, LucideIcon>)[name];
  // 매칭 실패 시 placeholder로 Cog. nav 변경 시 빌드 깨짐을 막고 시각적으로 식별 가능하게.
  return Icon ?? icons.Cog;
}
