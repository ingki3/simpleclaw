# Implementation Plan: Telegram Bot & Webhook Event Listener

**Branch**: `008-telegram-webhook` | **Date**: 2026-04-18 | **Spec**: [spec.md](./spec.md)

## Summary

Implement Telegram bot integration (long polling, whitelist auth) and a lightweight webhook HTTP server (aiohttp) for external event triggers.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: `python-telegram-bot>=21.0` (Telegram), `aiohttp>=3.9` (webhook server)
**Testing**: pytest + pytest-asyncio
**Project Type**: Library modules integrated with daemon

## Constitution Check

All principles PASS. Using lightweight dependencies (python-telegram-bot, aiohttp), Python-only, config-driven auth.

## Project Structure

```text
src/simpleclaw/
├── channels/
│   ├── __init__.py
│   ├── models.py          # WebhookEvent, AccessAttempt
│   ├── telegram_bot.py    # TelegramBot: polling, whitelist, message handling
│   └── webhook_server.py  # WebhookServer: aiohttp, auth, event handling
├── config.py              # (existing) — extend with telegram/webhook config

tests/
├── unit/
│   ├── test_telegram_bot.py
│   └── test_webhook_server.py
└── integration/
    └── test_channels_pipeline.py
```
