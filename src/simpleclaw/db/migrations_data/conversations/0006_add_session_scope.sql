-- BIZ-523: isolate conversation history and pending interactions by session.
-- Existing rows intentionally remain in the legacy-default scope; the
-- migration never guesses user/channel identity from message content.

ALTER TABLE messages
    ADD COLUMN session_key TEXT NOT NULL DEFAULT 'legacy-default';
ALTER TABLE messages
    ADD COLUMN turn_id TEXT;

CREATE INDEX IF NOT EXISTS idx_messages_session_id
    ON messages(session_key, id);
CREATE INDEX IF NOT EXISTS idx_messages_turn_id
    ON messages(turn_id);

CREATE TABLE IF NOT EXISTS conversation_sessions (
    session_key TEXT PRIMARY KEY,
    pending_kind TEXT,
    pending_payload TEXT,
    last_completed_turn_id TEXT,
    updated_at TEXT NOT NULL
);
