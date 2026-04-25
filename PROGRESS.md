# Development Progress

## Phase 1: Foundation (기반 체제 및 CLI)
- [x] 페르소나 설정 파싱 엔진 및 프롬프트 인젝터
- [x] 다중 LLM API 연동 및 외부 CLI 툴 서브프로세스 래핑

## Phase 2: Extension & Memory (확장 도구 및 메모리 구조 통합)
- [x] 로컬 전용/전역 스킬 모듈 및 MCP 클라이언트
- [x] Recipe 실행 엔진
- [x] 시맨틱 메모리 연동 및 드리밍 파이프라인

## Phase 3: Autonomy & Automation (통신 인터페이스와 스케줄링)
- [x] Heartbeat 데몬 및 Cron 스케줄러
- [x] 서브 에이전트 동적 호출 모델
- [x] 텔레그램 봇 및 Webhook 이벤트 리스너

## Phase 4: Expansion (플랫폼 고도화)
- [x] STT/TTS 인터페이스
- [x] 로깅 및 웹 대시보드

## Phase 5: Security & Multi-Turn (보안 강화 및 자율 실행)
- [x] 위험 명령 감지 Guard (35개+ 패턴)
- [x] Subprocess 시크릿 스트리핑
- [x] 프로세스 그룹 격리 (os.setsid + killpg)
- [x] 멀티턴 도구 실행 루프
