"""파일 기반 SQLite 스키마 마이그레이션 러너.

주요 동작 흐름:
1. ``MigrationRunner(db_path, migrations_dir)`` 인스턴스 생성.
2. ``run()`` 호출 시:
   - ``schema_version`` 메타 테이블이 없으면 생성한다.
   - **베이스라인 흡수**: ``schema_version``이 비어 있고, DB에 이미 0001이 정의하는
     테이블 집합이 모두 존재하면(= 마이그레이션 체계 도입 이전부터 사용 중인 DB),
     0001을 "이미 적용됨"으로 표시만 하고 SQL은 실행하지 않는다.
     이렇게 해야 기존 사용자가 데이터 손실 없이 마이그레이션 체계로 이행할 수 있다.
   - 미적용 마이그레이션을 버전 오름차순으로 실행한다.
   - 매 마이그레이션 실행 직전 DB 파일을 ``<db>.backup-<version>-<ts>``로 복사한다.
   - 실행 중 예외 발생 시 백업으로 원복(rename)하고 ``MigrationError``를 raise한다.
3. 부팅 시 ``run_conversations_migrations()`` / ``run_daemon_migrations()`` 헬퍼가
   각 스토어의 기본 마이그레이션 디렉토리를 자동으로 적용한다.

설계 결정:
- **자체 SQL 파일 기반**: Alembic은 SQLAlchemy 종속성이 크고, SimpleClaw는
  순수 sqlite3만 사용하므로 100줄 미만의 자체 러너가 더 적합하다.
- **DB별 마이그레이션 디렉토리 분리**: conversations와 daemon은 독립적으로
  진화하므로 버전 번호 충돌을 피하려고 디렉토리를 분리한다.
- **파일명 규칙**: ``NNNN_description.sql`` (예: ``0001_initial.sql``).
  ``NNNN``은 4자리 0-패딩, 단조 증가. 동일 버전 중복은 에러.
- **트랜잭션**: 각 마이그레이션은 단일 트랜잭션 내에서 실행한다.
  다만 SQLite는 일부 DDL(예: PRAGMA, ALTER TABLE의 일부)에서 자동 커밋이
  발생할 수 있으므로, 진정한 원자성은 파일 백업/복원으로 보장한다.
- **체크섬**: 적용된 마이그레이션 파일의 SHA-256을 기록해 사후 변경을 탐지한다.
  체크섬 불일치 시 경고 로그만 남기고 동작은 계속한다(개발 중 SQL 미세 수정 허용).
- **백업 정리**: 성공한 마이그레이션의 백업은 자동 삭제하지 않는다.
  운영자가 필요 시 수동 정리한다(데이터 손실 방어를 우선).
- **:memory: DB**: ``db_path == ":memory:"``인 경우 백업/복원을 건너뛴다
  (테스트 편의).
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# 패키지 내장 마이그레이션 디렉토리 — run_*_migrations 헬퍼가 사용한다
_PACKAGE_DIR = Path(__file__).resolve().parent
_BUILTIN_MIGRATIONS_ROOT = _PACKAGE_DIR / "migrations_data"

# 파일명 규칙: 4자리 버전 번호 + _ + 설명 + .sql
_MIGRATION_FILENAME_RE = re.compile(r"^(\d{4})_([A-Za-z0-9_\-]+)\.sql$")

# 베이스라인(0001) 흡수 시, 이미 존재해야 하는 테이블 집합.
# 새 DB와 레거시 DB를 구분하기 위함이다.
_BASELINE_TABLES: dict[str, frozenset[str]] = {
    "conversations": frozenset({"messages", "semantic_clusters"}),
    "daemon": frozenset({"cron_jobs", "cron_executions", "wait_states", "daemon_state"}),
}


class MigrationError(RuntimeError):
    """마이그레이션 적용·롤백 중 발생한 오류.

    ``__cause__``에 원인 예외가 연결되며, 메시지에는 어떤 버전에서 실패했는지
    기록한다. 부팅 경로에서는 이 예외를 잡아 종료/알림 정책을 결정한다.
    """


@dataclass(frozen=True)
class Migration:
    """단일 마이그레이션 파일 표현."""

    version: int
    name: str
    path: Path

    @property
    def sql(self) -> str:
        """파일 내용을 UTF-8로 읽어 반환한다."""
        return self.path.read_text(encoding="utf-8")

    @property
    def checksum(self) -> str:
        """파일 SHA-256 해시. 적용 후 변경 탐지에 사용한다."""
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


class MigrationRunner:
    """파일 기반 SQLite 스키마 마이그레이션 러너.

    스레드 안전하지 않다. 부팅 시 한 번만 호출하는 것을 가정한다.
    """

    SCHEMA_VERSION_TABLE = "schema_version"

    def __init__(
        self,
        db_path: str | Path,
        migrations_dir: str | Path,
        *,
        baseline_tables: Iterable[str] | None = None,
    ) -> None:
        """러너를 초기화한다.

        Args:
            db_path: 대상 SQLite DB 파일 경로. 부모 디렉토리가 없으면 생성한다.
                ``":memory:"``를 넘기면 인메모리 DB로 동작하지만, 이 경우 같은 연결을
                계속 재사용할 수 없으므로 보통 테스트에서만 의미가 있다.
            migrations_dir: 마이그레이션 SQL 파일이 있는 디렉토리.
            baseline_tables: 0001이 도입하는 테이블 이름들. ``schema_version``이
                비어있는데 이 테이블들이 모두 존재하면, 0001을 적용된 것으로 간주한다.
                None이면 베이스라인 흡수를 비활성화하고 항상 실행한다.
        """
        self._db_path = str(db_path)
        self._migrations_dir = Path(migrations_dir)
        self._baseline_tables = (
            frozenset(baseline_tables) if baseline_tables is not None else None
        )
        self._is_memory = self._db_path == ":memory:"

        if not self._is_memory:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def run(self) -> list[int]:
        """미적용 마이그레이션을 모두 적용하고, 적용된 버전 리스트를 반환한다.

        Raises:
            MigrationError: 디스크에서 SQL 파일 로드 실패, 버전 충돌, 또는
                개별 마이그레이션 실행 실패. 실행 실패 시 백업으로 DB를 원복한다.
        """
        migrations = self._discover_migrations()
        applied: list[int] = []

        with sqlite3.connect(self._db_path) as conn:
            self._ensure_meta_table(conn)
            already_applied = self._load_applied_versions(conn)

            # 베이스라인 흡수: 첫 마이그레이션이 0001이고, schema_version이 비어 있고,
            # 베이스라인 테이블이 이미 모두 존재하면 SQL 실행 없이 기록만 한다.
            if (
                migrations
                and not already_applied
                and migrations[0].version == 1
                and self._baseline_tables is not None
                and self._baseline_already_exists(conn)
            ):
                logger.info(
                    "DB %s: baseline tables already exist — recording migration "
                    "0001 as applied without executing SQL",
                    self._db_path,
                )
                self._record_applied(conn, migrations[0])
                already_applied.add(1)

            # 체크섬 불일치 경고 (이미 적용된 마이그레이션의 파일이 변경됐는지)
            self._warn_checksum_drift(conn, migrations, already_applied)

        # 미적용 항목만 순서대로 적용 (각 마이그레이션은 자체 트랜잭션)
        for migration in migrations:
            if migration.version in already_applied:
                continue
            self._apply_one(migration)
            applied.append(migration.version)

        if applied:
            logger.info(
                "DB %s: applied migrations %s", self._db_path, applied
            )
        return applied

    def current_version(self) -> int:
        """현재 적용된 마이그레이션 중 최댓값(없으면 0)을 반환한다."""
        with sqlite3.connect(self._db_path) as conn:
            self._ensure_meta_table(conn)
            row = conn.execute(
                f"SELECT MAX(version) FROM {self.SCHEMA_VERSION_TABLE}"
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _discover_migrations(self) -> list[Migration]:
        """디렉토리에서 마이그레이션 파일을 발견하고 버전 오름차순으로 반환한다.

        파일명 규칙(``NNNN_name.sql``)을 따르지 않으면 무시한다(README 등 동거 허용).
        동일 버전이 두 번 이상 등장하면 ``MigrationError``를 raise한다.
        """
        if not self._migrations_dir.exists():
            raise MigrationError(
                f"Migrations directory not found: {self._migrations_dir}"
            )

        migrations: dict[int, Migration] = {}
        for path in sorted(self._migrations_dir.iterdir()):
            if not path.is_file():
                continue
            match = _MIGRATION_FILENAME_RE.match(path.name)
            if not match:
                continue
            version = int(match.group(1))
            name = match.group(2)
            if version in migrations:
                raise MigrationError(
                    f"Duplicate migration version {version}: "
                    f"{migrations[version].path.name} vs {path.name}"
                )
            migrations[version] = Migration(version=version, name=name, path=path)

        return [migrations[v] for v in sorted(migrations)]

    def _ensure_meta_table(self, conn: sqlite3.Connection) -> None:
        """``schema_version`` 메타 테이블을 생성한다(멱등)."""
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.SCHEMA_VERSION_TABLE} (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL,
                checksum TEXT NOT NULL
            )
            """
        )
        conn.commit()

    def _load_applied_versions(self, conn: sqlite3.Connection) -> set[int]:
        """이미 적용된 버전 집합을 반환한다."""
        rows = conn.execute(
            f"SELECT version FROM {self.SCHEMA_VERSION_TABLE}"
        ).fetchall()
        return {int(r[0]) for r in rows}

    def _baseline_already_exists(self, conn: sqlite3.Connection) -> bool:
        """베이스라인 테이블이 모두 DB에 존재하는지 확인한다.

        ``schema_version``이 비어있는데 베이스라인 테이블이 존재하면,
        마이그레이션 체계 도입 이전부터 사용 중인 DB로 간주하고 0001을 흡수한다.
        """
        if self._baseline_tables is None:
            return False
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        existing = {r[0] for r in rows}
        return self._baseline_tables.issubset(existing)

    def _record_applied(
        self, conn: sqlite3.Connection, migration: Migration
    ) -> None:
        """마이그레이션 적용 사실을 ``schema_version``에 기록한다."""
        conn.execute(
            f"INSERT INTO {self.SCHEMA_VERSION_TABLE} "
            "(version, name, applied_at, checksum) VALUES (?, ?, ?, ?)",
            (
                migration.version,
                migration.name,
                datetime.now().isoformat(),
                migration.checksum,
            ),
        )
        conn.commit()

    def _warn_checksum_drift(
        self,
        conn: sqlite3.Connection,
        migrations: list[Migration],
        applied: set[int],
    ) -> None:
        """이미 적용된 마이그레이션의 체크섬이 변경됐는지 검사한다.

        개발 중 SQL 파일을 미세 수정하는 경우가 있어 에러로 처리하지는 않는다.
        대신 경고 로그를 남겨 운영자가 인지할 수 있게 한다.
        """
        rows = conn.execute(
            f"SELECT version, checksum FROM {self.SCHEMA_VERSION_TABLE}"
        ).fetchall()
        recorded = {int(v): c for v, c in rows}
        for m in migrations:
            if m.version not in applied:
                continue
            previous = recorded.get(m.version)
            if previous is not None and previous != m.checksum:
                logger.warning(
                    "Migration %04d_%s checksum changed since apply "
                    "(was %s, now %s) — manual review recommended",
                    m.version, m.name, previous[:8], m.checksum[:8],
                )

    def _apply_one(self, migration: Migration) -> None:
        """단일 마이그레이션을 백업과 함께 적용한다.

        실행 중 예외가 발생하면 백업 파일에서 DB를 원복하고
        ``MigrationError``를 raise한다.
        """
        backup_path = self._backup_db(migration)
        logger.info(
            "Applying migration %04d_%s to %s",
            migration.version, migration.name, self._db_path,
        )
        try:
            with sqlite3.connect(self._db_path) as conn:
                # executescript는 자체 트랜잭션을 시작하지만, 다중 문장 DDL을
                # 하나로 실행하기에는 가장 적합하다.
                conn.executescript(migration.sql)
                # 같은 트랜잭션 내에서 schema_version에 기록한다.
                self._record_applied(conn, migration)
        except Exception as exc:
            self._restore_backup(backup_path)
            raise MigrationError(
                f"Migration {migration.version:04d}_{migration.name} failed; "
                f"DB restored from backup {backup_path}"
            ) from exc

    def _backup_db(self, migration: Migration) -> Path | None:
        """마이그레이션 적용 직전 DB 파일을 백업한다.

        :memory: DB이거나 DB 파일이 아직 존재하지 않으면(첫 마이그레이션 직전 빈 파일)
        백업할 대상이 없으므로 None을 반환한다. 호출자는 None일 때 복원도 건너뛴다.
        """
        if self._is_memory:
            return None
        db_file = Path(self._db_path)
        if not db_file.exists() or db_file.stat().st_size == 0:
            return None
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        backup = db_file.with_name(
            f"{db_file.name}.backup-{migration.version:04d}-{ts}"
        )
        shutil.copy2(db_file, backup)
        # WAL/SHM 사이드카도 함께 백업하여 일관성 있는 복원 가능하게 한다.
        # 단, 마지막 connection 이 닫힐 때 SQLite 가 사이드카를 truncate/삭제할
        # 수 있어 exists() 와 copy2() 사이에서 파일이 사라지는 race 가 있다.
        # exists() 를 빼고 EAFP 로 잡아 누락을 무해 처리한다 — 사이드카가 없으면
        # 백업할 데이터도 없으므로(이미 메인 DB 파일에 fsync 됨) 일관성 손실이
        # 없다.
        for suffix in ("-wal", "-shm"):
            sidecar = db_file.with_name(db_file.name + suffix)
            try:
                shutil.copy2(sidecar, Path(str(backup) + suffix))
            except FileNotFoundError:
                continue
        logger.info("Backed up DB to %s before migration %04d",
                    backup, migration.version)
        return backup

    def _restore_backup(self, backup_path: Path | None) -> None:
        """백업에서 DB 파일을 원복한다.

        백업이 없으면(첫 마이그레이션이 빈 DB에서 실패했거나 :memory:) DB 파일을
        삭제하여 다음 부팅에서 처음부터 다시 시도하도록 한다.
        """
        if self._is_memory:
            return
        db_file = Path(self._db_path)
        if backup_path is None:
            # 백업이 없다 = 마이그레이션 시작 시 DB가 비어있었음.
            # 이 경우 schema_version 테이블만 있을 수 있으므로 깨끗이 지운다.
            for f in (db_file,
                      db_file.with_name(db_file.name + "-wal"),
                      db_file.with_name(db_file.name + "-shm")):
                if f.exists():
                    f.unlink()
            logger.warning("Removed empty/partial DB %s after migration failure",
                           db_file)
            return

        # WAL/SHM은 충돌 방지를 위해 먼저 제거 후 본 파일을 덮어쓴다.
        for suffix in ("-wal", "-shm"):
            sidecar = db_file.with_name(db_file.name + suffix)
            if sidecar.exists():
                sidecar.unlink()
        shutil.copy2(backup_path, db_file)
        for suffix in ("-wal", "-shm"):
            backup_sidecar = Path(str(backup_path) + suffix)
            if backup_sidecar.exists():
                shutil.copy2(backup_sidecar,
                             db_file.with_name(db_file.name + suffix))
        logger.warning(
            "Restored DB %s from backup %s after migration failure",
            db_file, backup_path,
        )


# ----------------------------------------------------------------------
# 부팅 시 사용하는 편의 헬퍼
# ----------------------------------------------------------------------

def run_conversations_migrations(db_path: str | Path) -> list[int]:
    """대화 DB에 패키지 내장 마이그레이션을 적용한다.

    ``ConversationStore.__init__``에서 ``_ensure_schema()`` 대신 호출되어,
    부팅 시 자동으로 스키마를 최신화한다.
    """
    runner = MigrationRunner(
        db_path,
        _BUILTIN_MIGRATIONS_ROOT / "conversations",
        baseline_tables=_BASELINE_TABLES["conversations"],
    )
    return runner.run()


def run_daemon_migrations(db_path: str | Path) -> list[int]:
    """데몬 DB에 패키지 내장 마이그레이션을 적용한다.

    ``DaemonStore.__init__``에서 ``_create_tables()`` 대신 호출되어,
    부팅 시 자동으로 스키마를 최신화한다.
    """
    runner = MigrationRunner(
        db_path,
        _BUILTIN_MIGRATIONS_ROOT / "daemon",
        baseline_tables=_BASELINE_TABLES["daemon"],
    )
    return runner.run()
