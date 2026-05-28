-- Migration 0003 (BIZ-307): 장기기억 retrieval read model.
--
-- conversations.db에 additive하게 memory_items를 추가한다. Dreaming/InsightStore와
-- cluster summary가 응답 시점 retrieval에서 함께 쓰는 장기기억 인덱스이다.

CREATE TABLE IF NOT EXISTS memory_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    text TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    source_ref TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.0,
    importance REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'active',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    last_accessed TEXT,
    embedding BLOB,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT,
    source_msg_ids TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_memory_items_type_status_updated
    ON memory_items (type, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_items_status_confidence_importance
    ON memory_items (status, confidence DESC, importance DESC);

CREATE INDEX IF NOT EXISTS idx_memory_items_source
    ON memory_items (source, source_ref);
