"""MigrationRunner 단위 테스트.

검증 항목:
1. 미적용 마이그레이션 적용 + schema_version 기록
2. 멱등성: 두 번 호출해도 한 번만 적용
3. 베이스라인 흡수: 기존 테이블이 있으면 0001을 SQL 실행 없이 기록
4. 실패 시 백업 복원 (롤백)
5. 첫 마이그레이션이 빈 DB에서 실패하면 partial DB 정리
6. 실 패키지 헬퍼(run_conversations_migrations / run_daemon_migrations) 동작
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from simpleclaw.db.migrations import (
    MigrationError,
    MigrationRunner,
    run_conversations_migrations,
    run_daemon_migrations,
)


def _write_migration(dir_path: Path, version: int, name: str, sql: str) -> Path:
    """헬퍼: 테스트용 마이그레이션 파일을 작성하고 경로를 반환."""
    path = dir_path / f"{version:04d}_{name}.sql"
    path.write_text(sql, encoding="utf-8")
    return path


@pytest.fixture
def migrations_dir(tmp_path: Path) -> Path:
    d = tmp_path / "migrations"
    d.mkdir()
    return d


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


class TestMigrationRunnerBasics:
    def test_applies_pending_migrations_in_order(
        self, db_path: Path, migrations_dir: Path
    ) -> None:
        _write_migration(
            migrations_dir, 1, "init",
            "CREATE TABLE foo (id INTEGER PRIMARY KEY, val TEXT);",
        )
        _write_migration(
            migrations_dir, 2, "add_bar",
            "CREATE TABLE bar (id INTEGER PRIMARY KEY);",
        )

        applied = MigrationRunner(db_path, migrations_dir).run()

        assert applied == [1, 2]
        with sqlite3.connect(db_path) as conn:
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert {"foo", "bar", "schema_version"}.issubset(tables)

    def test_is_idempotent(
        self, db_path: Path, migrations_dir: Path
    ) -> None:
        _write_migration(
            migrations_dir, 1, "init",
            "CREATE TABLE foo (id INTEGER PRIMARY KEY);",
        )
        runner = MigrationRunner(db_path, migrations_dir)

        first = runner.run()
        second = runner.run()

        assert first == [1]
        assert second == []  # 두 번째 호출은 적용할 게 없다

    def test_current_version_reports_max_applied(
        self, db_path: Path, migrations_dir: Path
    ) -> None:
        _write_migration(migrations_dir, 1, "a", "CREATE TABLE t1 (id INT);")
        _write_migration(migrations_dir, 2, "b", "CREATE TABLE t2 (id INT);")
        runner = MigrationRunner(db_path, migrations_dir)

        assert runner.current_version() == 0
        runner.run()
        assert runner.current_version() == 2

    def test_partial_apply_then_resume(
        self, db_path: Path, migrations_dir: Path
    ) -> None:
        """0001만 있는 상태에서 적용 → 0002 추가 후 재실행 시 0002만 적용."""
        _write_migration(migrations_dir, 1, "a", "CREATE TABLE t1 (id INT);")
        runner = MigrationRunner(db_path, migrations_dir)
        runner.run()

        _write_migration(migrations_dir, 2, "b", "CREATE TABLE t2 (id INT);")
        applied = MigrationRunner(db_path, migrations_dir).run()
        assert applied == [2]

    def test_duplicate_version_raises(
        self, db_path: Path, migrations_dir: Path
    ) -> None:
        _write_migration(migrations_dir, 1, "a", "CREATE TABLE t (id INT);")
        # 같은 버전을 다른 이름으로 또 만든다
        (migrations_dir / "0001_b.sql").write_text(
            "CREATE TABLE u (id INT);", encoding="utf-8",
        )
        with pytest.raises(MigrationError, match="Duplicate migration"):
            MigrationRunner(db_path, migrations_dir).run()

    def test_missing_directory_raises(self, db_path: Path, tmp_path: Path) -> None:
        with pytest.raises(MigrationError, match="not found"):
            MigrationRunner(db_path, tmp_path / "nonexistent").run()

    def test_non_migration_files_are_ignored(
        self, db_path: Path, migrations_dir: Path
    ) -> None:
        _write_migration(migrations_dir, 1, "init", "CREATE TABLE t (id INT);")
        (migrations_dir / "README.md").write_text("notes", encoding="utf-8")
        (migrations_dir / "0002.sql").write_text(  # 패턴 불일치(이름 없음)
            "CREATE TABLE skipped (id INT);", encoding="utf-8",
        )

        applied = MigrationRunner(db_path, migrations_dir).run()
        assert applied == [1]


class TestBaselineAbsorption:
    def test_absorbs_baseline_when_tables_already_exist(
        self, db_path: Path, migrations_dir: Path
    ) -> None:
        """schema_version이 비어있고 베이스라인 테이블이 이미 있으면
        SQL 실행 없이 0001을 적용된 것으로 기록한다."""
        # 레거시 DB 시뮬레이션: 베이스라인 테이블을 직접 만든다
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE foo (id INT, legacy_data TEXT)")
            conn.execute("INSERT INTO foo VALUES (1, 'preserved')")

        # 마이그레이션 0001은 같은 테이블이지만 다른 컬럼으로 정의되어 있다.
        # 베이스라인 흡수가 동작하면 SQL이 실행되지 않으므로 legacy_data가 보존됨.
        _write_migration(
            migrations_dir, 1, "init",
            "CREATE TABLE foo (id INT, val TEXT);",  # 다른 컬럼
        )

        applied = MigrationRunner(
            db_path, migrations_dir, baseline_tables={"foo"},
        ).run()

        assert applied == []  # SQL 실행 안 됨
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT legacy_data FROM foo WHERE id=1"
            ).fetchone()
            assert row[0] == "preserved"  # 데이터 손실 없음
            ver_row = conn.execute(
                "SELECT version FROM schema_version"
            ).fetchone()
            assert ver_row[0] == 1  # 베이스라인 기록은 남음

    def test_does_not_absorb_when_baseline_tables_missing(
        self, db_path: Path, migrations_dir: Path
    ) -> None:
        """베이스라인 테이블이 없으면 정상 적용 경로를 탄다."""
        _write_migration(
            migrations_dir, 1, "init", "CREATE TABLE foo (id INT);",
        )
        applied = MigrationRunner(
            db_path, migrations_dir, baseline_tables={"foo"},
        ).run()
        assert applied == [1]

    def test_no_absorption_when_baseline_tables_none(
        self, db_path: Path, migrations_dir: Path
    ) -> None:
        """``baseline_tables=None``이면 흡수 비활성화 — 항상 SQL 실행."""
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE foo (id INT)")
        # 같은 이름의 테이블을 다시 만들려고 하면 충돌해야 하지만,
        # CREATE TABLE IF NOT EXISTS가 아니므로 실패 → 롤백 경로 확인용.
        _write_migration(
            migrations_dir, 1, "init", "CREATE TABLE foo (val TEXT);",
        )
        with pytest.raises(MigrationError):
            MigrationRunner(db_path, migrations_dir).run()


class TestBackupAndRollback:
    def test_failed_migration_restores_from_backup(
        self, db_path: Path, migrations_dir: Path
    ) -> None:
        """0001 적용 후 0002가 실패하면 0001 상태로 복원된다."""
        _write_migration(
            migrations_dir, 1, "init",
            "CREATE TABLE foo (id INTEGER PRIMARY KEY, val TEXT);",
        )
        runner = MigrationRunner(db_path, migrations_dir)
        runner.run()
        with sqlite3.connect(db_path) as conn:
            conn.execute("INSERT INTO foo (val) VALUES ('keep me')")

        # 의도적으로 실패하는 0002를 추가
        _write_migration(
            migrations_dir, 2, "broken",
            "CREATE TABLE bar (id INT); SELECT * FROM nonexistent_table;",
        )

        with pytest.raises(MigrationError, match="0002_broken"):
            MigrationRunner(db_path, migrations_dir).run()

        # 데이터는 보존돼야 한다 (백업에서 복원)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT val FROM foo").fetchone()
            assert row[0] == "keep me"
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            # bar는 만들어지지 않았어야 한다 (롤백 성공)
            assert "bar" not in tables
            # schema_version은 여전히 v1만 갖고 있어야 한다
            versions = {
                r[0] for r in conn.execute(
                    "SELECT version FROM schema_version"
                ).fetchall()
            }
            assert versions == {1}

    def test_failed_first_migration_cleans_partial_db(
        self, db_path: Path, migrations_dir: Path
    ) -> None:
        """빈 DB에 첫 마이그레이션이 실패하면 partial DB가 깨끗이 정리된다."""
        _write_migration(
            migrations_dir, 1, "broken",
            "CREATE TABLE foo (id INT); SELECT * FROM nonexistent;",
        )
        with pytest.raises(MigrationError):
            MigrationRunner(db_path, migrations_dir).run()

        # 다음 부팅에서 처음부터 재시도할 수 있도록 partial 파일이 없어야 한다.
        # (또는 schema_version 테이블만 있는 비어있는 상태)
        if db_path.exists():
            with sqlite3.connect(db_path) as conn:
                tables = {
                    r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                assert "foo" not in tables  # partial 테이블이 남아있지 않음

    def test_backup_file_is_created_before_apply(
        self, db_path: Path, migrations_dir: Path
    ) -> None:
        """기존 DB가 있으면 마이그레이션 적용 직전 백업이 만들어진다."""
        _write_migration(migrations_dir, 1, "a", "CREATE TABLE t1 (id INT);")
        MigrationRunner(db_path, migrations_dir).run()

        _write_migration(migrations_dir, 2, "b", "CREATE TABLE t2 (id INT);")
        MigrationRunner(db_path, migrations_dir).run()

        backups = list(db_path.parent.glob("test.db.backup-0002-*"))
        assert len(backups) == 1, f"expected 1 backup, found {backups}"


class TestPackagedHelpers:
    """패키지 내장 마이그레이션 헬퍼가 실제 SQL 파일을 적용하는지 확인."""

    def test_run_conversations_migrations_creates_expected_tables(
        self, tmp_path: Path
    ) -> None:
        db = tmp_path / "conv.db"
        applied = run_conversations_migrations(db)
        assert 1 in applied
        with sqlite3.connect(db) as conn:
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert {"messages", "semantic_clusters", "schema_version"}.issubset(tables)

    def test_run_daemon_migrations_creates_expected_tables(
        self, tmp_path: Path
    ) -> None:
        db = tmp_path / "daemon.db"
        applied = run_daemon_migrations(db)
        assert 1 in applied
        with sqlite3.connect(db) as conn:
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert {
            "cron_jobs", "cron_executions", "wait_states",
            "daemon_state", "schema_version",
        }.issubset(tables)

    def test_legacy_conversations_db_is_absorbed_without_data_loss(
        self, tmp_path: Path
    ) -> None:
        """마이그레이션 체계 도입 이전 형태의 DB에 데이터를 넣은 뒤
        헬퍼를 호출해도 데이터가 보존되어야 한다."""
        db = tmp_path / "legacy.db"
        # 레거시 스키마를 직접 만들어 데이터를 채운다 (기존 ConversationStore 모양)
        with sqlite3.connect(db) as conn:
            conn.executescript("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    token_count INTEGER DEFAULT 0,
                    embedding BLOB,
                    cluster_id INTEGER
                );
                CREATE TABLE semantic_clusters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT NOT NULL DEFAULT '',
                    centroid BLOB NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    member_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO messages (role, content, timestamp)
                    VALUES ('user', 'legacy hello', '2026-01-01T00:00:00');
            """)

        applied = run_conversations_migrations(db)
        assert applied == []  # 베이스라인 흡수 → SQL 실행 안 됨

        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT content FROM messages WHERE role='user'"
            ).fetchone()
            assert row[0] == "legacy hello"
            ver = conn.execute(
                "SELECT MAX(version) FROM schema_version"
            ).fetchone()[0]
            assert ver == 1
