-- Migration 0001: 데몬 저장소 베이스라인 스키마.
--
-- DaemonStore._create_tables()가 종래 in-line으로 생성하던 4개 테이블을
-- 마이그레이션 0번으로 흡수한다. 기존 DB는 MigrationRunner가 베이스라인
-- 흡수 로직(_baseline_already_exists)으로 SQL을 실행하지 않고 적용 기록만
-- 남긴다.

-- 크론 작업 정의.
CREATE TABLE IF NOT EXISTS cron_jobs (
    name TEXT PRIMARY KEY,
    cron_expression TEXT NOT NULL,
    action_type TEXT NOT NULL,
    action_reference TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 크론 실행 로그.
CREATE TABLE IF NOT EXISTS cron_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    result_summary TEXT DEFAULT '',
    error_details TEXT DEFAULT '',
    FOREIGN KEY (job_name) REFERENCES cron_jobs(name)
);

-- Wait state(에이전트 비동기 대기) 영속화.
CREATE TABLE IF NOT EXISTS wait_states (
    task_id TEXT PRIMARY KEY,
    serialized_state TEXT NOT NULL,
    condition_type TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    timeout_seconds INTEGER NOT NULL DEFAULT 3600,
    resolved_at TEXT,
    resolution TEXT
);

-- 데몬 키-값 상태(가벼운 메타데이터 저장).
CREATE TABLE IF NOT EXISTS daemon_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
