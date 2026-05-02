/**
 * 클래스명 결합 유틸 — clsx의 얇은 alias.
 * 디자인 시스템 컴포넌트는 variant·state별 토큰 클래스를 조합하므로
 * 어디에나 한 글자 길이의 임포트로 부르기 쉽도록 별도 파일로 둔다.
 */
import clsx, { type ClassValue } from "clsx";

export function cn(...inputs: ClassValue[]): string {
  return clsx(inputs);
}
