/**
 * Vitest setup — 모든 테스트에서 공통으로 적용되는 환경 정리.
 *
 * MSW는 각 테스트 파일에서 `setupServer`로 직접 핸들러를 정의해 사용한다
 * (글로벌 핸들러를 둘 만큼 케이스가 많지 않음).
 */

// ResizeObserver / matchMedia 등의 jsdom 미지원 API stub은 필요 시 여기에 추가.
