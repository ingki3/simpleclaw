# 텔레그램 봇

SimpleClaw의 주요 사용자 인터페이스입니다. 화이트리스트 기반 접근 제어로 인가된 사용자만 에이전트와 대화할 수 있습니다.

## 설정

```yaml
telegram:
  bot_token_env: "TELEGRAM_BOT_TOKEN"
  whitelist:
    user_ids: [YOUR_TELEGRAM_USER_ID]
    chat_ids: []
```

### 봇 토큰 설정

1. Telegram에서 [@BotFather](https://t.me/BotFather)에게 `/newbot` 명령
2. 발급받은 토큰을 `.env`에 설정:

```env
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ
```

### 사용자 ID 확인

1. Telegram에서 [@userinfobot](https://t.me/userinfobot)에게 메시지 전송
2. 표시되는 ID를 `whitelist.user_ids`에 추가

## 접근 제어

### Fail-Closed 정책

화이트리스트가 비어있으면 모든 메시지를 거부합니다. 허용할 사용자/채팅 ID를 명시적으로 등록해야 합니다.

### 접근 로그

모든 접근 시도(인가/거부)가 `AccessAttempt`으로 기록됩니다:

```
WARNING: Unauthorized Telegram access: user=9876543, chat=9876543
```

## 메시지 처리 흐름

```
Telegram 메시지 수신
  ↓
화이트리스트 확인
  ├── 거부 → 응답 없음 + 로그 기록
  └── 허용 ↓
메시지 텍스트 추출 (최대 4096자)
  ↓
AgentOrchestrator.process_message() 호출
  ↓
응답 전송
```

## 슬래시 명령어

`/`로 시작하는 메시지는 레시피 명령어로 처리됩니다:

```
/morning-briefing          → morning-briefing 레시피 실행
/morning-briefing date=... → 파라미터 전달
```

## Cron 알림

Cron job 결과는 화이트리스트의 첫 번째 `user_id`에게 전송됩니다. `[NO_NOTIFY]` 응답인 경우 전송하지 않습니다.

## 실행

```bash
# 포그라운드
.venv/bin/python scripts/test_telegram.py

# 백그라운드
nohup .venv/bin/python scripts/test_telegram.py > .agent/bot.log 2>&1 &
```

## 관련 파일

- `src/simpleclaw/channels/telegram_bot.py` — TelegramBot 클래스
- `src/simpleclaw/channels/models.py` — AccessAttempt 모델
- `scripts/test_telegram.py` — 봇 실행 스크립트
