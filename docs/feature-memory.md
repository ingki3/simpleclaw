# 대화 기억 및 드리밍

SimpleClaw는 대화를 SQLite에 저장하고, 심야 시간에 자동으로 요약하여 장기 기억을 형성합니다.

## 대화 저장소

모든 사용자-에이전트 대화는 `.agent/conversations.db`에 저장됩니다.

### 히스토리 주입

LLM 호출 시 최근 대화를 자동으로 포함합니다:

```yaml
agent:
  history_limit: 20    # 최근 20개 메시지를 컨텍스트에 포함
```

### Cron 메시지 격리

Cron job으로 실행되는 메시지는 대화 히스토리에 저장되지 않으며, 이전 대화 컨텍스트도 포함하지 않습니다. 이를 통해 Cron 결과가 일반 대화에 의해 오염되는 것을 방지합니다.

## 드리밍 파이프라인

에이전트가 유휴 상태일 때 대화 히스토리를 LLM으로 요약하여 `.agent/MEMORY.md`에 기록합니다.

### 트리거 조건

- **시각**: `overnight_hour` (기본 03:00)
- **유휴**: `idle_threshold` (기본 7200초, 2시간) 동안 활동 없음

### 동작 방식

1. 마지막 드리밍 이후의 대화를 수집
2. LLM에게 요약 요청
3. 요약본을 `MEMORY.md`에 추가
4. 수정 전 `.bak` 백업 파일 자동 생성

### 설정

```yaml
daemon:
  dreaming:
    overnight_hour: 3       # 드리밍 실행 시각 (03:00)
    idle_threshold: 7200    # 유휴 판단 기준 (초)
```

### 무결성 원칙

드리밍은 `MEMORY.md`만 수정합니다. `AGENT.md`와 `USER.md`는 드리밍 과정에서 읽기 전용으로 유지됩니다.

## 관련 파일

- `src/simpleclaw/memory/conversation_store.py` — SQLite 대화 저장소
- `src/simpleclaw/memory/models.py` — ConversationMessage, MessageRole
- `src/simpleclaw/memory/dreaming.py` — 드리밍 파이프라인
- `src/simpleclaw/daemon/dreaming_trigger.py` — 드리밍 트리거 조건 판단
