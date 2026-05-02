-- Migration 0001: 대화 저장소 베이스라인 스키마.
--
-- ConversationStore._ensure_schema()가 종래 in-line으로 생성하던 테이블을
-- 마이그레이션 0번으로 흡수한다. 기존 DB는 MigrationRunner가 베이스라인
-- 흡수 로직(_baseline_already_exists)으로 SQL을 실행하지 않고 적용 기록만
-- 남긴다 — 따라서 이 파일을 수정해도 기존 DB의 데이터에는 영향이 없다.
--
-- 신규 컬럼/인덱스 추가는 0002_*.sql 이후 파일로 분리한다.

-- WAL 저널 모드: 데몬·드리밍 동시 쓰기 시 잠금 충돌 완화.
-- PRAGMA는 영구 적용이며 멱등하다.
PRAGMA journal_mode = WAL;

-- 대화 메시지: id 단조 증가, 임베딩과 클러스터 멤버십을 컬럼으로 보유.
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    token_count INTEGER DEFAULT 0,
    embedding BLOB,
    cluster_id INTEGER
);

-- 시맨틱 클러스터 인덱스 (Phase 3, spec 005).
-- 외래 키 제약은 두지 않는다 — 클러스터 삭제 시 messages.cluster_id가
-- dangling이어도 동작 무관(ConversationStore가 None처럼 처리).
CREATE TABLE IF NOT EXISTS semantic_clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL DEFAULT '',
    centroid BLOB NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    member_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
