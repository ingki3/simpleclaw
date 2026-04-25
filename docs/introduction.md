# 소개

## SimpleClaw란?

SimpleClaw는 Python으로 구현된 개인 비서 AI 에이전트입니다. 사용자의 일상 업무를 자율적으로 처리하며, 텔레그램을 통해 언제 어디서든 소통할 수 있습니다.

## 핵심 개념

### 에이전트 오케스트레이터

SimpleClaw의 중심에는 `AgentOrchestrator`가 있습니다. 사용자 메시지를 받으면 다음 파이프라인을 거칩니다:

```
메시지 수신
  ↓
페르소나 로드 (AGENT.md, USER.md)
  ↓
ReAct Loop (Thought → Action → Observation → Answer)
  ↓
Thought: LLM이 다음 행동을 추론
  ↓
Action: 스킬 실행 → Observation: 결과 관찰 → 반복 (최대 5회)
  ↓
Answer: 충분한 정보가 모이면 최종 응답 생성 (페르소나 + 히스토리 + 도구 결과 포함)
  ↓
대화 저장 + 응답 전송
```

### 멀티턴 도구 실행 (ReAct)

단순한 1회성 도구 호출이 아니라, ReAct(Reasoning + Acting) 패턴으로 LLM이 단계적으로 추론하며 도구를 호출합니다. 예를 들어 "오늘 메일 확인하고 일정도 알려줘"라는 요청에:

1. **Thought**: "메일을 먼저 확인해야 한다"
2. **Action**: Gmail 스킬 실행 → **Observation**: 메일 목록 획득
3. **Thought**: "이제 일정도 확인해야 한다"
4. **Action**: Calendar 스킬 실행 → **Observation**: 오늘 일정 획득
5. **Thought**: "두 결과를 종합할 수 있다"
6. **Answer**: 메일과 일정을 종합하여 최종 응답 생성

도구 실행이 실패하면 LLM이 다른 접근법을 시도하며, 모든 시도가 실패하면 정직하게 실패를 알립니다.

### 파일 기반 설정

모든 설정은 파일로 관리됩니다:

| 파일 | 역할 |
|------|------|
| `config.yaml` | LLM, 스킬, 보안 등 전체 설정 |
| `.agent/AGENT.md` | 에이전트의 성격과 행동 규칙 |
| `.agent/USER.md` | 사용자 프로필과 선호도 |
| `.agent/MEMORY.md` | 장기 기억 (드리밍으로 자동 업데이트) |
| `.agent/workspace/` | 스킬 파일 출력 디렉토리 (격리된 작업 공간) |
| `.env` | API 키 (GOOGLE_API_KEY 등) |

설정 파일을 수정하면 다음 메시지부터 자동 반영됩니다. 재시작이 필요 없습니다.

## 프로젝트 구조

```
SimpleClaw/
├── src/simpleclaw/
│   ├── agent.py           # 중앙 오케스트레이터
│   ├── config.py          # 설정 로더
│   ├── persona/           # 페르소나 시스템
│   ├── llm/               # 다중 LLM 라우팅
│   ├── skills/            # 스킬 디스커버리 및 실행
│   ├── recipes/           # YAML 레시피 워크플로우
│   ├── security/          # 명령 실행 보안
│   ├── memory/            # 대화 저장소 및 드리밍
│   ├── daemon/            # Heartbeat 및 Cron 스케줄러
│   ├── agents/            # 서브 에이전트
│   ├── channels/          # 텔레그램 봇, Webhook
│   ├── voice/             # STT/TTS 음성 처리
│   └── logging/           # 구조화 로거 및 대시보드
├── .agent/                # 런타임 데이터 (페르소나, 스킬, 레시피, DB)
├── config.yaml            # 전체 설정
├── scripts/               # 실행 스크립트
└── tests/                 # 테스트 코드
```

## 설계 원칙

1. **Python 전용** — 코어 런타임에 다른 언어 의존성 없음
2. **경량 의존성** — asyncio + APScheduler + SQLite만 사용, Docker/Redis 불필요
3. **설정 기반** — 모든 동작을 `config.yaml`과 `.env`로 제어
4. **다층 보안** — 위험 명령 감지, 시크릿 필터링, 프로세스 격리, 화이트리스트
5. **즉시 반영** — 설정 파일 수정 시 재시작 없이 다음 메시지부터 자동 적용 (lazy loading)
6. **ReAct 추론** — LLM이 Thought/Action/Observation 사이클로 단계적 추론 수행
