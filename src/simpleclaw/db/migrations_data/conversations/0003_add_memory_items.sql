-- Migration 0003 (BIZ-307): DB-backed 장기기억 항목 read model.
--
-- MEMORY.md/USER.md bullet 파서와 InsightStore sidecar를 즉시 대체하지 않고,
-- 후속 Admin UI/API 전환이 참조할 안정적인 id 기반 저장소를 additive 하게 추가한다.

CREATE TABLE IF NOT EXISTS memory_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    text TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT,
    source_msg_ids TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_memory_items_type_status_updated
    ON memory_items (type, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_items_status_updated
    ON memory_items (status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_items_source
    ON memory_items (source);
