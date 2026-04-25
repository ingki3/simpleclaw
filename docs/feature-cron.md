# Cron 스케줄러

Cron 스케줄러는 APScheduler 기반으로 예약 작업을 관리합니다. 사용자가 자연어로 요청하면 LLM이 Cron 표현식으로 변환합니다.

## 사용 예시

텔레그램에서 자연어로 요청:

```
"30분마다 메일 확인해줘"
→ cron job: */30 * * * *, action: "읽지 않은 메일 확인해줘"

"매일 아침 9시에 아침 브리핑 해줘"
→ cron job: 0 9 * * *, action: recipe "morning-briefing"

"주중 오후 6시에 퇴근 알림 보내줘"
→ cron job: 0 18 * * 1-5, action: "오늘 하루 마무리 요약해줘"
```

## Cron 표현식

```
분  시  일  월  요일
*   *   *   *   *

예시:
*/30 * * * *     → 30분마다
0 9 * * *        → 매일 09:00
0 9 * * 1-5      → 주중 09:00
0 */2 * * *      → 2시간마다
```

## 작업 유형

| 유형 | 설명 | action_reference |
|------|------|-----------------|
| `prompt` | 프롬프트를 에이전트 파이프라인으로 처리 | 프롬프트 텍스트 |
| `recipe` | v2 레시피를 실행 | 레시피 이름 |

## 알림 제어 (NO_NOTIFY)

Cron job 실행 결과가 "알릴 내용 없음"일 때 텔레그램 알림을 보내지 않습니다. 불필요한 알림을 방지하여 사용자 경험을 개선합니다.

### 동작 방식

1. Cron 프롬프트에 `[CRON JOB]` 컨텍스트가 자동 추가됨
2. LLM에게 "알릴 내용이 없으면 응답의 첫 줄에 `[NO_NOTIFY]`를 포함하라" 지시
3. 텔레그램 봇이 응답을 확인하여 `[NO_NOTIFY]`가 포함되면 전송을 생략

### 예시: 새 메일 모니터링

```
Cron 설정: "30분마다 읽지 않은 메일 확인해줘. 새 메일이 있을 때만 알려줘."
→ cron job: */30 * * * *, action: "읽지 않은 메일 확인해줘. 새 메일이 있을 때만 알려줘."

[실행 1] 새 메일 없음:
  LLM 응답: "[NO_NOTIFY] 새 메일이 없습니다."
  → 텔레그램 전송 생략 (사용자에게 알림 없음)

[실행 2] 새 메일 3건:
  LLM 응답: "새 메일 3건이 도착했습니다:\n1. ..."
  → 텔레그램 전송 (사용자에게 알림)
```

## 관리 명령

LLM이 내장 명령으로 처리:

- **생성**: "30분마다 메일 확인해줘"
- **목록**: "등록된 cron job 보여줘"
- **삭제**: "메일 확인 cron job 삭제해줘"
- **활성화/비활성화**: "메일 확인 중지해줘" / "다시 켜줘"

## 실행 격리 (process_cron_message)

Cron job은 `process_cron_message()`로 실행되며, 일반 대화와 완전히 격리됩니다:

- **히스토리 미로드**: 대화 히스토리를 컨텍스트에 포함하지 않음 (isolated=True)
- **저장 안 함**: Cron 결과를 대화 DB에 저장하지 않음
- **맥락 분리**: 이전 대화 맥락이 Cron 결과에 영향을 주지 않음
- **ReAct 루프 사용**: 격리된 상태에서도 동일한 ReAct 파이프라인을 통해 스킬 호출 가능

`prompt`와 `recipe` 두 작업 유형 모두 `process_cron_message()`를 통해 처리됩니다.

## 영속성

Cron job은 `.agent/daemon.db`(SQLite)에 저장됩니다. 봇을 재시작해도 등록된 작업이 자동으로 복원됩니다.

## 설정

```yaml
daemon:
  heartbeat_interval: 300    # Heartbeat 주기 (초)
  db_path: ".agent/daemon.db"
```

## 관련 파일

- `src/simpleclaw/daemon/scheduler.py` — CronScheduler, APScheduler 래퍼
- `src/simpleclaw/daemon/store.py` — SQLite 영속성
- `src/simpleclaw/daemon/models.py` — CronJob, ActionType, ExecutionStatus
