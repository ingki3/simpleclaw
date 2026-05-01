# SimpleClaw Documentation

SimpleClaw는 **확장 가능한 개인 비서 AI 에이전트**입니다. Python으로 구현되었으며, 여러 LLM을 유연하게 전환하고, 스킬과 레시피를 통해 기능을 확장하며, 텔레그램으로 언제 어디서든 소통할 수 있습니다.

## 문서 목차

### 시작하기
- [소개](introduction.md) — SimpleClaw의 핵심 개념과 아키텍처
- [설치 및 실행](installation.md) — 환경 설정, 의존성 설치, 봇 실행

### 주요 기능
- [페르소나 시스템](feature-persona.md) — AGENT.md, USER.md로 에이전트 성격 정의
- [다중 LLM 라우팅](feature-llm.md) — Claude, Gemini, GPT-4o 멀티 프로바이더
- [스킬 시스템](feature-skills.md) — 자동 디스커버리, MCP 통합, 스킬 개발 가이드
- [레시피 워크플로우](feature-recipes.md) — YAML 기반 재사용 가능한 자동화 워크플로우
- [대화 기억 및 드리밍](feature-memory.md) — 대화 저장, 시맨틱 메모리(RAG), 클러스터 기반 그래프형 드리밍
- [Cron 스케줄러](feature-cron.md) — 예약 작업, 자동 실행, 알림 제어
- [텔레그램 봇](feature-telegram.md) — 화이트리스트 인증, 메시지 처리, Cron 알림
- [보안](feature-security.md) — 위험 명령 감지, 환경변수 필터링, 프로세스 격리
- [서브에이전트 응답 프로토콜](feature-subagent-protocol.md) — 서브에이전트 stdout JSON 표준 스키마와 검증 규약

## 아키텍처 개요

```
사용자 (Telegram / Webhook)
    ↓
AgentOrchestrator
    ├── ReAct Loop         ← Thought → Action → Observation → Answer
    ├── PersonaAssembler   ← AGENT.md, USER.md, MEMORY.md
    ├── LLMRouter          ← Claude / Gemini / GPT-4o
    ├── SkillExecutor      ← 스킬 디스커버리 + MCP + CommandGuard
    ├── RecipeExecutor     ← YAML 레시피 (/recipe-name 슬래시 명령)
    ├── ConversationStore  ← SQLite 대화 히스토리 + 임베딩/클러스터 (RAG)
    ├── EmbeddingService   ← sentence-transformers 다국어 임베딩 (lazy-load)
    ├── Workspace          ← 스킬 파일 출력 격리 (.agent/workspace)
    ├── CronScheduler      ← APScheduler 예약 실행
    └── SubAgentSpawner    ← 격리된 서브 에이전트
```
