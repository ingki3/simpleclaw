CREATE TABLE IF NOT EXISTS graph_outbound_persistence (
    persistence_id TEXT PRIMARY KEY,
    payload_hash TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
