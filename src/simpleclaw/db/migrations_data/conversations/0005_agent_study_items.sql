-- Migration 0005 (BIZ-388): Agent Study Wiki 의 구조화 retrieval index.
--
-- Markdown 위키가 source of truth 이지만, 질문 시 빠른 retrieval 과 freshness
-- filtering 에는 구조화 index 가 필요하다. 사용자 메모리(memory_items)와 섞이지
-- 않도록 study_topics / study_items 로 분리해 conversations.db 에 additive 하게
-- 추가한다. 두 저장소의 경계(외부 세계 배경지식 vs 사용자 자신)는 설계 문서
-- docs/agent-study-wiki.md 의 핵심 불변식이다.

CREATE TABLE IF NOT EXISTS study_topics (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    priority TEXT NOT NULL DEFAULT 'medium',
    tags_json TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'manual',
    interest_score REAL NOT NULL DEFAULT 0.0,
    importance_score REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS study_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id TEXT NOT NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    source_title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'unknown',
    confidence REAL NOT NULL DEFAULT 0.0,
    importance REAL NOT NULL DEFAULT 0.0,
    published_at TEXT,
    retrieved_at TEXT NOT NULL,
    valid_until TEXT,
    embedding BLOB,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(topic_id) REFERENCES study_topics(id)
);

CREATE INDEX IF NOT EXISTS idx_study_items_topic ON study_items(topic_id);
CREATE INDEX IF NOT EXISTS idx_study_items_retrieved_at ON study_items(retrieved_at);
CREATE INDEX IF NOT EXISTS idx_study_items_confidence ON study_items(confidence);
