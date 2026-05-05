/**
 * 클래스명 결합 유틸 — 의존성 없는 미니 구현.
 *
 * 디자인 시스템 컴포넌트는 variant·state 별 토큰 클래스를 다수 조합하므로
 * 짧은 import 한 번으로 안전하게 합치고 싶다. clsx 를 들이지 않은 이유는
 * Admin 2.0 의 의존성 표면을 최소화하기 위함이다 (S0 가이드라인).
 *
 * 허용 입력: 문자열, false-y (false/0/""/null/undefined → 무시), 객체 `{ cls: bool }`.
 */

export type ClassValue =
  | string
  | number
  | false
  | null
  | undefined
  | { [key: string]: unknown };

export function cn(...inputs: ClassValue[]): string {
  const out: string[] = [];
  for (const input of inputs) {
    if (!input) continue;
    if (typeof input === "string") {
      out.push(input);
    } else if (typeof input === "number") {
      out.push(String(input));
    } else if (typeof input === "object") {
      for (const [key, value] of Object.entries(input)) {
        if (value) out.push(key);
      }
    }
  }
  return out.join(" ");
}
