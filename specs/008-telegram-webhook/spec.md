# Feature Specification: Telegram Bot & Webhook Event Listener

**Feature Branch**: `008-telegram-webhook`  
**Created**: 2026-04-18  
**Status**: Draft

## User Scenarios & Testing

### User Story 1 - Telegram Bot Messaging (Priority: P1)

As a user, I want to send commands and receive responses from my agent via Telegram, so that I can interact with the agent from my phone or any device with Telegram.

**Why this priority**: Telegram is the primary external communication channel. Without it, the agent has no user-facing interface outside the local CLI.

**Independent Test**: Can be tested by sending a message to the bot, verifying the message is received and processed, and confirming a response is sent back.

**Acceptance Scenarios**:

1. **Given** the Telegram bot is configured and running, **When** the authorized user sends a text message, **Then** the bot receives the message, processes it, and sends a response back.
2. **Given** an unauthorized user sends a message, **When** the bot receives it, **Then** the message is silently dropped and logged as an unauthorized access attempt.
3. **Given** the bot is configured with a valid token, **When** the daemon starts, **Then** the Telegram bot starts polling for updates.
4. **Given** the bot encounters a Telegram API error, **When** the error occurs, **Then** the bot logs the error and continues polling without crashing.

---

### User Story 2 - Whitelist Access Control (Priority: P1)

As a security-conscious user, I want only my authorized Telegram accounts to interact with the bot, so that unauthorized users cannot issue commands to my agent.

**Why this priority**: Security is critical for an agent that can execute commands and access personal data.

**Independent Test**: Can be tested by sending messages from authorized and unauthorized user IDs and verifying only authorized messages are processed.

**Acceptance Scenarios**:

1. **Given** a whitelist of Telegram User IDs is configured, **When** a message arrives from a whitelisted user, **Then** the message is processed normally.
2. **Given** a whitelist is configured, **When** a message arrives from a non-whitelisted user, **Then** the message is rejected and the attempt is logged.
3. **Given** the whitelist is empty or not configured, **When** a message arrives, **Then** all messages are rejected (fail-closed).

---

### User Story 3 - Webhook Event Listener (Priority: P2)

As a user, I want a single REST webhook endpoint that can receive events from external services (Zapier, n8n, etc.), so that external triggers can invoke agent actions without complex integrations.

**Why this priority**: The webhook provides the event-driven automation bridge. It is important but the agent functions without it.

**Independent Test**: Can be tested by sending an HTTP POST to the webhook endpoint and verifying the event is received, validated, and processed.

**Acceptance Scenarios**:

1. **Given** the webhook server is running, **When** an external service sends a POST request with a valid JSON payload, **Then** the event is received, validated, and queued for processing.
2. **Given** the webhook server is running, **When** a request arrives without proper authentication (missing or invalid token), **Then** the request is rejected with a 401 status.
3. **Given** a valid webhook event is received, **When** the event contains an action reference (prompt or recipe), **Then** the action is executed and the result is logged.
4. **Given** the webhook server is running, **When** a malformed request arrives, **Then** the server returns a 400 status with an error description.

---

### Edge Cases

- What happens when the Telegram API is unreachable? The bot should retry with exponential backoff and log the connectivity issue.
- What happens when the webhook server port is already in use? The server should fail to start with a clear error message.
- What happens when a webhook event references a nonexistent recipe? The event should be logged as failed with a descriptive error.
- What happens when multiple webhook events arrive simultaneously? The server should handle concurrent requests without blocking.
- What happens when the Telegram bot receives a very long message? The message should be truncated to a reasonable limit before processing.

## Requirements

### Functional Requirements

- **FR-001**: System MUST integrate with the Telegram Bot API to receive and send messages via long polling.
- **FR-002**: System MUST authenticate incoming Telegram messages against a configurable whitelist of User IDs and Chat IDs.
- **FR-003**: System MUST silently drop and log messages from non-whitelisted Telegram users.
- **FR-004**: System MUST reject all Telegram messages when no whitelist is configured (fail-closed).
- **FR-005**: System MUST provide a single REST webhook endpoint (POST) that accepts JSON event payloads.
- **FR-006**: System MUST authenticate webhook requests using a configurable bearer token.
- **FR-007**: System MUST validate webhook payloads and return appropriate HTTP status codes (200, 400, 401).
- **FR-008**: System MUST support executing prompts or recipes as webhook event actions.
- **FR-009**: System MUST log all incoming messages, access attempts, and webhook events.
- **FR-010**: System MUST start/stop the Telegram bot and webhook server as part of the daemon lifecycle.

### Key Entities

- **TelegramBot**: The Telegram integration component. Attributes: bot token, whitelist, polling status, message handler.
- **WebhookServer**: The REST webhook receiver. Attributes: host, port, auth token, event handler.
- **WebhookEvent**: An incoming webhook payload. Attributes: event type, action type, action reference, payload data, timestamp.
- **AccessAttempt**: A log entry for authorized/unauthorized access. Attributes: source (telegram/webhook), user identifier, timestamp, authorized flag.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Authorized Telegram messages receive a response within 5 seconds under normal conditions.
- **SC-002**: Unauthorized access attempts are blocked and logged 100% of the time.
- **SC-003**: Webhook events are acknowledged (HTTP 200) within 1 second of receipt.
- **SC-004**: The system handles at least 10 concurrent webhook requests without errors.
- **SC-005**: All communication channel events are logged with timestamps.

## Assumptions

- The Telegram bot token is stored in `.env` or `config.yaml` and referenced via environment variable.
- The `python-telegram-bot` library is used for Telegram integration (lightweight, asyncio-compatible).
- The webhook server uses a lightweight HTTP framework (aiohttp or the built-in asyncio HTTP server) — NOT FastAPI to keep dependencies light per constitution.
- The webhook bearer token is a simple pre-shared secret configured in `config.yaml`.
- Message processing for Telegram responses will initially echo/log the message; full LLM integration comes when the agent loop is built.
- The webhook server listens on localhost by default (configurable host/port).
