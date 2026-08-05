# Production skill contract fixtures

이 디렉터리는 SimpleClaw CI가 host 전역 설치 경로에 의존하지 않고 production skill
계약을 fail-closed로 검증하기 위한 versioned snapshot입니다.

- upstream repository: `https://github.com/ingki3/skills`
- upstream source commit: `0990a9ba1c28751e0e52108bd7fb26f3305e7741`
- upstream PR: `https://github.com/ingki3/skills/pull/1`

실행 script의 동작 검증은 upstream 저장소가 소유하며, 여기에는 discovery와 Contract
Registry에 필요한 task-owned metadata만 둡니다.
