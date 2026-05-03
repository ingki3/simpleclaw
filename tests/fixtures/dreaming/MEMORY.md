# Core Memory

<!--
이 파일의 USER-OWNED 영역은 BIZ-66/BIZ-72의 "drift 전" 상태를 시뮬레이션한 것이다.
드리밍은 절대 이 영역을 건드려서는 안 된다 — 단 1바이트라도 변경되면 회귀 테스트가
fail해야 한다(잘 알려진 AGENT.md 30→2줄 사고의 MEMORY.md 버전 재현 방지).
-->

## Static facts (USER-OWNED — DREAMING MUST NOT TOUCH)

- 사용자(형님)는 SimpleClaw 메인 사용자이며 Korean speaker.
- Workspace `bizcatalyst`는 BIZ-* 이슈 prefix를 사용한다.
- 시스템 총 메모리: 48GB (2026-04-28 확인).

## Manual journal (USER-OWNED)

- 2026-04-29: 사용자가 발생했던 기술적 문제를 직접 해결함.

<!-- managed:dreaming:journal -->
<!-- /managed:dreaming:journal -->

<!-- managed:dreaming:clusters -->
<!-- /managed:dreaming:clusters -->

## After-marker user note (USER-OWNED)

- 마커 뒤 영역도 dreaming 침범 금지.
