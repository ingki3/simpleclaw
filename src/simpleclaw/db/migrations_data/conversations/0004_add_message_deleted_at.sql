-- Migration 0004 (BIZ-366): messages soft-delete flag for /undo context rewind.
--
-- /undo must exclude recent turns from future LLM context without physically deleting
-- conversation rows, so audit/debug views can still retrieve the original messages.
-- NULL means visible; non-NULL records the ISO timestamp when the row was hidden.

ALTER TABLE messages ADD COLUMN deleted_at TEXT;

CREATE INDEX IF NOT EXISTS idx_messages_visible_id
    ON messages (deleted_at, id);