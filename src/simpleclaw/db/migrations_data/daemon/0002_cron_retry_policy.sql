-- Migration 0002: Cron 작업 자동 재시도 정책 (BIZ-19).
--
-- 일시적 장애에서 자율 회복하기 위해 작업별 재시도 정책과 누적 실패 기반의
-- circuit-break 상태를 영속화한다. 모든 컬럼은 NOT NULL DEFAULT를 가지므로
-- 기존 행에는 안전한 기본값이 채워진다.

-- cron_jobs: 재시도 정책 + 누적 실패 카운터.
ALTER TABLE cron_jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3;
ALTER TABLE cron_jobs ADD COLUMN backoff_seconds REAL NOT NULL DEFAULT 60.0;
ALTER TABLE cron_jobs ADD COLUMN backoff_strategy TEXT NOT NULL DEFAULT 'exponential';
ALTER TABLE cron_jobs ADD COLUMN circuit_break_threshold INTEGER NOT NULL DEFAULT 5;
ALTER TABLE cron_jobs ADD COLUMN consecutive_failures INTEGER NOT NULL DEFAULT 0;

-- cron_executions: 동일 트리거 내 N번째 시도임을 식별.
ALTER TABLE cron_executions ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1;
