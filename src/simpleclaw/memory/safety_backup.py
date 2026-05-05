"""드리밍/데몬 라이브 파일의 사이클 단위 안전 스냅샷 (BIZ-132).

배경:
    BIZ-28(2026-05-05) 사고 — `git rm --cached`로 untrack된 ``.agent/{AGENT,USER,
    MEMORY,SOUL}.md`` 4종 라이브 페르소나 파일이 머지/체크아웃 시퀀스 중에
    dreaming pipeline이 동시에 쓰기를 시도하면서 working tree에서 사라졌다.
    사고의 본질은 "untrack 자체"가 아니라 "untrack된 파일을 런타임이 계속 write
    하는 상황" — 즉 외부 git 작업이 라이브 파일을 지운 직후의 race window.

이 모듈은 dreaming 사이클 진입 직전(``_preflight_protected_sections`` 호출 직전)에
지정된 위험 파일들을 ``.agent/_safety_backup/{YYYYMMDD_HHMMSS}/``에 통째로 복사해
두는 매니저를 제공한다. 복사 후의 사이클이 어떻게 진행되든(성공/스킵/실패) 백업
디렉터리는 그 시점의 라이브 상태를 보존한다.

설계 결정:
    - 디렉터리는 사이클 타임스탬프로 1:1 매핑 — 운영자가 "5월 5일 09:50 사이클
      직전 상태"를 즉시 가져갈 수 있도록 한다.
    - 보존 정책: 최근 ``max_cycles``개 유지 + 가장 최근 1개는 항상 보존
      (``max_cycles=0`` 같은 오설정에도 살아남는 안전 기본값).
    - DB 파일은 ``shutil.copy2`` 대신 ``sqlite3.Connection.backup`` API 로 atomic
      스냅샷을 만든다. WAL 활성 상태에서도 일관된 스냅샷을 보장.
    - 백업 자체의 실패는 사이클을 차단하지 않는다 — 백업이 안 되더라도 dreaming
      preflight 가드가 라이브 파일 손상을 방어한다. 백업 실패는 WARN 로그만 남기고
      사이클은 그대로 진행 (관측 가능하게).

`SafetyBackupManager.latest_backup_for(filename)`은 BIZ-132 Phase 2 의 preflight
자가 복원 흐름에서 호출된다 — "라이브 파일이 사라졌을 때 가장 최근 스냅샷에서
1회 한정으로 복원" 의 입력 소스가 된다.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


# 한 사이클 백업 디렉터리 이름 포맷. "YYYYMMDD_HHMMSS" — 사람이 읽기 쉽고
# 디렉터리 이름 정렬이 시간순과 일치한다.
TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"


@dataclass
class SafetyBackupManager:
    """위험 파일 목록을 사이클 직전에 스냅샷하고 보존 회전하는 매니저.

    Attributes:
        backup_root: ``.agent/_safety_backup`` 같은 백업 루트 디렉터리.
        files: 평범한 파일들 (Markdown/JSONL/TXT 등). 존재하지 않는 파일은 조용히 건너뛴다 —
            사이클이 처음 도는 환경에서 sidecar 파일이 아직 생성되지 않았어도 백업 자체는
            성공해야 하므로.
        databases: SQLite DB 파일들. ``sqlite3 .backup`` API 로 atomic 복사된다.
        max_cycles: 보존할 최대 사이클 수 (가장 최근 1개는 항상 보존, 오설정 방어).
        clock: 테스트 가능성을 위한 ``datetime.now`` 주입점.
    """

    backup_root: Path
    files: list[Path] = field(default_factory=list)
    databases: list[Path] = field(default_factory=list)
    max_cycles: int = 7
    clock: Callable[[], datetime] = datetime.now

    def __post_init__(self) -> None:
        # 외부에서 들어오는 경로/숫자를 정규화 — Path 객체로 통일하고 max_cycles
        # 를 최소 1로 제한해 "가장 최근 1개는 항상 보존" 보장이 자연스럽게 성립.
        self.backup_root = Path(self.backup_root)
        self.files = [Path(p) for p in self.files]
        self.databases = [Path(p) for p in self.databases]
        if self.max_cycles < 1:
            logger.warning(
                "SafetyBackupManager.max_cycles=%d < 1 — coerced to 1 (always keep latest).",
                self.max_cycles,
            )
            self.max_cycles = 1

    # ------------------------------------------------------------------
    # 스냅샷 생성
    # ------------------------------------------------------------------

    def snapshot(self) -> Path | None:
        """현재 시점의 위험 파일 목록을 새 사이클 디렉터리에 복사한다.

        Returns:
            생성된 백업 디렉터리 경로. 백업할 파일이 하나도 존재하지 않으면 None
            (사이클 진입 직전인데 라이브 파일이 모두 부재한 첫 부팅 상황 등).

        Raises:
            예외는 잡아서 WARN 로그로만 남기고 None 반환 — 백업 실패가 사이클을
            막지 않게 한다.
        """
        try:
            ts = self.clock().strftime(TIMESTAMP_FORMAT)
            target_dir = self._unique_target_dir(self.backup_root / ts)
            target_dir.mkdir(parents=True, exist_ok=True)

            copied_any = False
            for src in self.files:
                if self._copy_plain_file(src, target_dir):
                    copied_any = True
            for src in self.databases:
                if self._copy_database(src, target_dir):
                    copied_any = True

            if not copied_any:
                # 라이브 파일이 모두 부재 — 빈 백업 디렉터리는 보관하지 않는다.
                # 이런 상태는 첫 부팅 또는 사고 직후에만 발생.
                try:
                    target_dir.rmdir()
                except OSError:
                    pass
                logger.info(
                    "Safety backup skipped: no source files exist yet (%s)",
                    self.backup_root,
                )
                return None

            self._prune()
            logger.info("Safety backup snapshot created at %s", target_dir)
            return target_dir
        except Exception:
            # 백업 실패가 사이클을 막아서는 안 된다 — preflight/사이클 가드가 별도로 동작.
            logger.exception("Safety backup snapshot failed; cycle will continue")
            return None

    def _unique_target_dir(self, base: Path) -> Path:
        """동일 초 단위에 두 번 호출돼도 충돌하지 않도록 suffix를 붙인다.

        사람이 호출하는 일은 거의 없지만, 테스트나 빠른 재시도 시에 같은 1초 안에
        들어올 수 있다. 같은 초의 디렉터리가 이미 있으면 ``..._1``, ``..._2`` 형식으로
        suffix를 부여한다.
        """
        if not base.exists():
            return base
        idx = 1
        while True:
            candidate = base.with_name(f"{base.name}_{idx}")
            if not candidate.exists():
                return candidate
            idx += 1

    def _copy_plain_file(self, src: Path, target_dir: Path) -> bool:
        """평범한 파일을 그대로 복사. 없으면 조용히 스킵."""
        if not src.is_file():
            return False
        try:
            shutil.copy2(src, target_dir / src.name)
            return True
        except OSError:
            logger.exception("Failed to copy %s to safety backup", src)
            return False

    def _copy_database(self, src: Path, target_dir: Path) -> bool:
        """SQLite DB를 atomic backup API로 복사한다.

        ``shutil.copy2``는 WAL 활성 상태에서 페이지 일관성을 보장하지 못해 복원 시
        손상된 DB가 될 수 있다. ``Connection.backup``은 동시 쓰기 중에도 일관된
        스냅샷을 만들어내며 SQLite 공식 권장 방법.
        """
        if not src.is_file():
            return False
        dst = target_dir / src.name
        # DB 락 충돌은 흔하므로 짧은 재시도 — 본 매니저는 사이클 진입 직전 한 번만
        # 호출되므로 적당한 타임아웃이면 충분.
        src_conn = None
        dst_conn = None
        try:
            src_conn = sqlite3.connect(str(src), timeout=5.0)
            dst_conn = sqlite3.connect(str(dst))
            src_conn.backup(dst_conn)
            return True
        except sqlite3.Error:
            logger.exception("SQLite backup failed for %s", src)
            # 부분 생성된 dst 정리
            try:
                if dst.exists():
                    dst.unlink()
            except OSError:
                pass
            return False
        finally:
            if dst_conn is not None:
                dst_conn.close()
            if src_conn is not None:
                src_conn.close()

    # ------------------------------------------------------------------
    # 보존 회전
    # ------------------------------------------------------------------

    def _prune(self) -> None:
        """``max_cycles``를 초과한 오래된 사이클 디렉터리를 제거한다.

        사이클 디렉터리 이름은 ``YYYYMMDD_HHMMSS[_N]`` 형식이므로 사전식 정렬이
        시간순과 일치한다 — 별도 mtime 정렬 없이도 가장 오래된 것을 식별 가능.
        """
        if not self.backup_root.is_dir():
            return
        cycles = sorted(
            [p for p in self.backup_root.iterdir() if p.is_dir()],
            key=lambda p: p.name,
        )
        if len(cycles) <= self.max_cycles:
            return
        to_delete = cycles[: len(cycles) - self.max_cycles]
        for old in to_delete:
            try:
                shutil.rmtree(old)
                logger.debug("Pruned old safety backup cycle: %s", old)
            except OSError:
                logger.exception("Failed to prune old safety backup %s", old)

    # ------------------------------------------------------------------
    # 조회 (Phase 2 — 자가 복원 입력)
    # ------------------------------------------------------------------

    def latest_backup_for(self, filename: str) -> Path | None:
        """주어진 파일명(basename) 의 가장 최근 백업 사본 경로를 반환한다.

        모든 사이클 디렉터리를 최신순으로 훑어 첫 매치를 반환 — 가장 최근에 안전
        스냅샷된 버전이 우선된다. 매치되는 백업이 없으면 None.

        Args:
            filename: 라이브 파일의 basename (예: ``"MEMORY.md"``,
                ``"insights.jsonl"``, ``"conversations.db"``).

        Returns:
            가장 최근 백업 사본의 절대/상대 경로(매니저 설정에 따른 형태).
            매치 없음 → None.
        """
        if not self.backup_root.is_dir():
            return None
        cycles = sorted(
            (p for p in self.backup_root.iterdir() if p.is_dir()),
            key=lambda p: p.name,
            reverse=True,
        )
        for cycle in cycles:
            candidate = cycle / filename
            if candidate.is_file():
                return candidate
        return None

    def list_cycles(self) -> list[Path]:
        """모든 백업 사이클 디렉터리를 시간순(오래된 → 최신)으로 반환. 운영 가시성용."""
        if not self.backup_root.is_dir():
            return []
        return sorted(
            (p for p in self.backup_root.iterdir() if p.is_dir()),
            key=lambda p: p.name,
        )


def find_legacy_memory_backup(
    memory_backup_dir: Path, filename: str
) -> Path | None:
    """``.agent/memory-backup/`` 의 ``{stem}.{ts}.bak`` 형식 백업 중 최신본 1개를 찾는다.

    ``DreamingPipeline.create_backup`` 이 만드는 레거시 백업 형식 — 이 자체는 BIZ-132
    이전부터 존재했지만, 사이클 단위가 아닌 "파일별 최근 N건"이라 실수로 통째 삭제된
    경우의 복원 후보로는 부족했다. 그래도 Phase 2 자가 복원의 *2차* 후보로는 유효
    하므로 safety_backup 이 비었을 때 폴백으로 활용한다.

    Args:
        memory_backup_dir: ``.agent/memory-backup`` 같은 디렉터리.
        filename: 라이브 파일의 basename (예: ``"MEMORY.md"``).

    Returns:
        가장 최근 ``.bak`` 파일 경로. 매치 없음 → None.
    """
    if not memory_backup_dir.is_dir():
        return None
    stem = Path(filename).stem
    candidates = sorted(
        memory_backup_dir.glob(f"{stem}.*.bak"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


__all__ = [
    "SafetyBackupManager",
    "find_legacy_memory_backup",
    "TIMESTAMP_FORMAT",
]
