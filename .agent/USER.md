# User Profile

<!--
이 파일은 SimpleClaw 사용자 프로필이다. 세 영역으로 구성된다:

1. 마커 외부 영역(이 파일에서 ## Preferences 와 같은 자유 섹션):
   - 사용자가 직접 작성하고 관리한다.
   - 드리밍 파이프라인은 절대 손대지 않는다 (BIZ-72 Protected Section 모델).

2. `managed:dreaming:insights` 섹션:
   - 드리밍이 자동으로 추가하는 사용자 인사이트 영역이다.
   - 이 마커 안쪽에서만 dreaming의 추가가 허용된다.

3. `managed:dreaming:active-projects` 섹션 (BIZ-74):
   - 최근 N일(기본 7일) 대화에서 자동 추출한 "현재 집중 중인 프로젝트" 카드 영역.
   - 매 dreaming 사이클마다 in-place 갱신된다 — sidecar(.agent/active_projects.jsonl)가
     진실의 출처이며, 본 섹션은 윈도우 내 항목만 렌더링한 사람이 읽는 요약.

마커 자체를 삭제하면 드리밍이 fail-closed로 중단된다 (사용자 콘텐츠 보호 보장).
-->

## Preferences
- 1차 언어: 한국어 (BIZ-80 — dreaming 산출물도 한국어로 통일)
- 대화 스타일: 캐주얼하고 직설적
- 장황한 설명보다 간결한 답변 선호

<!-- managed:dreaming:insights -->
## Dreaming Insights (2026-04-28)
- 정치 뉴스와 시사 안보 이슈에 지속적인 관심을 보임
- 크론(Cron) 작업을 설정하여 이메일 확인 및 AI 리포트 수신을 자동화하여 사용함
- AI 에이전트 기술, 자율 실행 에이전트, AI 하드웨어 인프라와 같은 최신 기술 트렌드에 관심이 높음
- 맥북에어 15인치 구매를 고려 중이며 기기 사양 및 가격 정보에 민감함
- 고사양 시스템 환경(메모리 48GB)을 사용 중임

## Dreaming Insights (2026-04-29)
- 발생한 문제를 스스로 해결하려는 자기 주도적 성향을 보임
- '형님'과 같은 친근한 호칭을 사용하는 대화 방식에 거부감이 없음
<!-- /managed:dreaming:insights -->

<!-- managed:dreaming:active-projects -->
_최근 윈도우에 식별된 활성 프로젝트가 없습니다._
<!-- /managed:dreaming:active-projects -->
