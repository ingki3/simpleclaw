"""SimpleClaw 데이터베이스 마이그레이션 서브시스템.

각 SQLite 데이터베이스(conversations.db, daemon.db)의 스키마를 파일 기반
SQL 마이그레이션으로 관리한다. 자세한 설계는 ``migrations.py`` 참고.
"""

from simpleclaw.db.migrations import (
    MigrationError,
    MigrationRunner,
    run_conversations_migrations,
    run_daemon_migrations,
)

__all__ = [
    "MigrationError",
    "MigrationRunner",
    "run_conversations_migrations",
    "run_daemon_migrations",
]
