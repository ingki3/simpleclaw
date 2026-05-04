# Memory

<!--
SimpleClaw의 일자별 핵심 기억(MEMORY.md). 두 영역으로 구성된다:

1. 마커 외부 영역:
   - 사용자가 직접 적은 메모/맥락. 드리밍은 절대 손대지 않는다.

2. `managed:dreaming:journal` 섹션:
   - 드리밍 사이클이 일자별 사실/이벤트를 append하는 영역.
   - 마커 안쪽에서만 dreaming의 시간순 append가 허용된다.

3. `managed:dreaming:clusters` 섹션:
   - Phase 3 그래프형 드리밍이 활성화된 경우 ``cluster:N start`` 등의
     클러스터 섹션이 이 컨테이너 안에서만 upsert된다.

마커 자체를 삭제·변형하면 드리밍이 fail-closed로 중단된다 (BIZ-72).
-->

<!-- managed:dreaming:journal -->
## 2026-04-28
- 현재 시스템의 총 메모리 사이즈(48GB)를 확인 함
- 크론 작업 목록(check_new_emails, ai-report)을 확인 함
- 당일 주요 정치 뉴스(윤석열 전 대통령 항소심, 이재명 대통령 판문점선언 8주년, 6·3 지방선거 준비)를 요약 받음
- AI 아침 브리핑(/ai-report)을 통해 구글 클라우드 Next 2026, 엔비디아 시총 5조 달러 돌파, 8세대 TPU 공개, 자율 보안 에이전트 관련 소식을 확인 함
- 맥북에어 15인치 모델(M5, M4, M2)의 가격 정보를 확인 함

## 2026-04-29
- 사용자가 발생했던 기술적 문제를 직접 해결함.
<!-- /managed:dreaming:journal -->

<!-- managed:dreaming:clusters -->
<!-- /managed:dreaming:clusters -->
