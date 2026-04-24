# 페르소나 시스템

페르소나 시스템은 에이전트의 성격, 행동 규칙, 사용자 정보를 정의합니다. 마크다운 파일로 관리되며, 매 메시지 처리 시 파일에서 읽어 시스템 프롬프트에 주입됩니다.

## 파일 구성

| 파일 | 역할 | 수정 주체 |
|------|------|----------|
| `.agent/AGENT.md` | 에이전트의 이름, 성격, 행동 규칙 | 사용자 |
| `.agent/USER.md` | 사용자 프로필, 선호도, 언어 | 사용자 |
| `.agent/MEMORY.md` | 장기 기억, 대화 요약 | 드리밍 파이프라인 (자동) |

## AGENT.md 작성 예시

```markdown
# SimpleClaw Agent

You are **SimpleClaw**, a personal assistant AI agent.

## Core Behavior
- You are helpful, concise, and friendly.
- You respond in the same language the user writes in.
- You keep responses under 500 characters for Telegram readability.
- 사용자를 존댓말(~요/~습니다)로 대할 것.

## Identity
- Name: SimpleClaw
- Role: Personal Assistant Agent
- Platform: Telegram messaging

## Google Calendar
사용자의 캘린더 목록:
- **Primary** — 개인 기본 캘린더
- **Family** — 가족 일정
```

## USER.md 작성 예시

```markdown
# User Profile

## Preferences
- Primary language: Korean (한국어)
- Communication style: Casual, direct
- Timezone: Asia/Seoul (KST, UTC+9)
```

## 동작 방식

1. `_build_system_prompt()`가 호출될 때마다 파일을 디스크에서 읽음
2. `resolver.py`가 로컬(`.agent/`) → 전역(`~/.agents/main/`) 순으로 탐색
3. `parser.py`가 마크다운을 섹션별로 파싱
4. `assembler.py`가 토큰 예산(`token_budget: 4096`) 내에서 프롬프트 조립
5. 조립된 프롬프트가 LLM 호출 시 시스템 프롬프트로 사용

## 로컬 vs 전역 우선순위

```
로컬: .agent/AGENT.md         ← 우선 사용
전역: ~/.agents/main/AGENT.md  ← 로컬에 없을 때 사용
```

동일한 타입의 파일이 로컬과 전역 모두에 있으면 로컬이 우선합니다.

## 즉시 반영

파일을 수정하면 다음 메시지부터 자동으로 반영됩니다. 봇 재시작이 필요 없습니다.

## 관련 파일

- `src/simpleclaw/persona/models.py` — PersonaFile, PromptAssembly 모델
- `src/simpleclaw/persona/parser.py` — 마크다운 파싱
- `src/simpleclaw/persona/assembler.py` — 토큰 예산 관리 및 프롬프트 조립
- `src/simpleclaw/persona/resolver.py` — 로컬/전역 파일 탐색
