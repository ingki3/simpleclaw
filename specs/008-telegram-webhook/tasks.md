# Tasks: Telegram Bot & Webhook Event Listener

## Phase 1: Setup

- [x] T001 Add `python-telegram-bot>=21.0` and `aiohttp>=3.9` to dependencies in pyproject.toml
- [x] T002 Add telegram and webhook configuration sections to config.yaml
- [x] T003 Extend config loader with `load_telegram_config()` and `load_webhook_config()` in src/simpleclaw/config.py
- [x] T004 Create channels package directory and __init__.py in src/simpleclaw/channels/__init__.py

---

## Phase 2: Foundational

- [x] T005 Create channel models (WebhookEvent, AccessAttempt) in src/simpleclaw/channels/models.py

---

## Phase 3: User Story 1+2 — Telegram Bot with Whitelist (Priority: P1)

- [x] T006 [US1] Implement TelegramBot with polling, message handling, whitelist auth, start/stop lifecycle in src/simpleclaw/channels/telegram_bot.py
- [x] T007 [US1] Export public API in src/simpleclaw/channels/__init__.py

---

## Phase 4: User Story 3 — Webhook Server (Priority: P2)

- [x] T008 [US3] Implement WebhookServer with aiohttp, bearer token auth, JSON validation, event processing in src/simpleclaw/channels/webhook_server.py
- [x] T009 [US3] Export WebhookServer in src/simpleclaw/channels/__init__.py

---

## Phase 5: Polish & Tests

- [x] T010 [P] Write unit tests for TelegramBot (whitelist, message handling) in tests/unit/test_telegram_bot.py
- [x] T011 [P] Write unit tests for WebhookServer (auth, validation, events) in tests/unit/test_webhook_server.py
- [x] T012 Write integration test for channels pipeline in tests/integration/test_channels_pipeline.py
- [x] T013 Run full test suite and fix any failures
