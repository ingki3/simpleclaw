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

Cron job으로 실행되는 메시지는 `process_cron_message()`를 통해 격리 처리됩니다. 자세한 내용은 아래 [Cron 메시지 격리](#cron-메시지-격리) 섹션을 참고하세요.

## 드리밍 파이프라인

에이전트가 유휴 상태일 때 대화 히스토리를 LLM으로 요약하여 `.agent/MEMORY.md`에 기록합니다.

### 트리거 조건

- **시각**: `overnight_hour` (기본 03:00)
- **유휴**: `idle_threshold` (기본 7200초, 2시간) 동안 활동 없음

### 동작 방식

1. `MEMORY.md`와 `USER.md`의 `.bak` 백업 파일 자동 생성
2. 마지막 드리밍 이후의 대화를 수집
3. LLM에게 대화 분석 요청 (구조화된 JSON 추출)
4. 추출된 내용을 각각 해당 파일에 추가:
   - **memory** (사실, 이벤트, 결정 사항) → `MEMORY.md`에 추가
   - **user_insights** (선호도, 관심사, 습관) → `USER.md`에 추가

### 설정

```yaml
daemon:
  dreaming:
    overnight_hour: 3       # 드리밍 실행 시각 (03:00)
    idle_threshold: 7200    # 유휴 판단 기준 (초)
    model: "gemini"         # 드리밍에 사용할 LLM (선택, 미지정 시 기본 LLM 사용)
```

`dreaming.model`을 지정하면 드리밍 요약에 특정 LLM 백엔드를 사용합니다. 예를 들어 일반 대화는 Claude를 쓰면서 드리밍은 비용이 낮은 Gemini로 처리할 수 있습니다.

### 무결성 원칙

- 드리밍은 `MEMORY.md`와 `USER.md`만 수정합니다.
- **`AGENT.md`는 드리밍 과정에서 절대 수정되지 않습니다** (읽기 전용). 에이전트의 성격과 행동 규칙은 사용자가 직접 편집해야 합니다.
- `USER.md`에는 대화에서 명확히 드러난 정보만 추가되며, 민감한 개인정보(비밀번호, 금융정보)는 저장하지 않습니다.

## Cron 메시지 격리

Cron job에서 발생하는 메시지는 `process_cron_message()`를 통해 처리되며, 일반 대화와 완전히 격리됩니다:

- 대화 히스토리를 로드하지 않음 (isolated 모드)
- 대화 DB에 저장하지 않음
- Cron 결과가 일반 대화 컨텍스트에 영향을 주지 않음

이를 통해 Cron 결과가 이전 대화에 의해 오염되거나, 반대로 Cron 출력이 일반 대화에 혼입되는 것을 방지합니다.

## 관련 파일

- `src/simpleclaw/memory/conversation_store.py` — SQLite 대화 저장소
- `src/simpleclaw/memory/models.py` — ConversationMessage, MessageRole
- `src/simpleclaw/memory/dreaming.py` — 드리밍 파이프라인
- `src/simpleclaw/daemon/dreaming_trigger.py` — 드리밍 트리거 조건 판단
