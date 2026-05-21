"""Per-turn file mutation tracker — disk delta footer for ReAct observations (BIZ-251).

매 ReAct iteration 직후, 에이전트가 실제로 디스크에 무엇을 썼는지를
다음 LLM 호출 컨텍스트에 자동 첨부하기 위한 유틸리티. LLM 이 "파일 저장
했다" 고 환각하거나, 스킬 실패를 인식 못 한 채 다음 단계로 넘어가는
사고를 잡는 verifier footer 의 데이터 소스.

설계 결정:
- 스냅샷은 ``(size, mtime_ns, line_count)`` 세 필드만 적재한다. 컨텐츠
  자체나 hash 는 저장하지 않으므로 1000+ 파일 워크스페이스에서도 100KB
  미만의 메모리만 사용한다.
- ``line_count`` 는 비변경 파일에 대해 이전 스냅샷에서 재사용된다
  (``snapshot(previous=...)``). 매 턴 모든 파일을 다시 카운트하지 않아
  large workspace 의 추가 latency 를 stat 비용 한도로 묶는다.
- 워크스페이스 루트는 재귀 walk, 페르소나 루트는 명시 파일 화이트리스트
  (AGENT.md / USER.md / MEMORY.md) 만 추적한다. ``~/.simpleclaw/`` 전체를
  walk 하면 SQLite WAL, embedding cache, recipes 등 운영 파일이 다 노이즈로
  올라온다 — 사고 회피.
- 바이너리 / 큰 파일은 line_count 를 생략 (``bytes``) 한다. ``\\n`` 으로
  세는 단순 카운터이므로 BOM/encoding 영향 거의 없다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# ``line_count`` 비용 상한. 200KB 초과 파일은 라인 카운트를 생략하여
# pathological 대용량 텍스트(예: 누적 dreaming 로그) 가 매 턴 비용을 늘리지
# 않게 한다. 200KB 는 약 4~6천 줄 텍스트로 일반 페르소나 파일/스킬 출력
# 한도를 충분히 커버.
_DEFAULT_LINE_COUNT_MAX_BYTES = 200 * 1024

# walk 비용 상한. 워크스페이스가 비정상적으로 부풀면 walk 자체가 무거워
# 지므로 hard cap 을 두고 도달 시 경고만 남기고 추적을 끊는다.
_DEFAULT_MAX_FILES = 50_000

# 워크스페이스 재귀 walk 에서 건너뛸 디렉터리 이름. SQLite/캐시/git 메타가
# 매 턴 mtime 이 바뀌어 footer 를 노이즈로 채우는 것을 차단.
_SKIP_DIRNAMES = frozenset(
    {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        ".venv",
        "venv",
    }
)

# 워크스페이스 재귀 walk 에서 건너뛸 파일 확장자/이름. SQLite WAL/SHM 처럼
# 도구가 의도적으로 쓰지 않은 부산물이 footer 로 새는 것을 막는다.
_SKIP_SUFFIXES = (
    "-wal",
    "-shm",
    ".pyc",
    ".pyo",
    ".swp",
    ".swo",
)
_SKIP_FILENAMES = frozenset({".DS_Store"})


@dataclass(frozen=True)
class FileEntry:
    """단일 추적 파일의 스냅샷 엔트리."""

    size: int
    mtime_ns: int
    # ``\\n`` 기준 라인 카운트. ``None`` 이면 라인 카운트 생략 (큰 파일 / 읽기
    # 실패 / 바이너리). footer 렌더 시 ``bytes`` 로 fallback.
    line_count: int | None


@dataclass(frozen=True)
class FileChange:
    """diff 결과의 단일 변경 레코드."""

    display_path: str
    kind: str  # "added" | "modified" | "deleted"
    new_lines: int | None
    new_bytes: int | None
    old_lines: int | None
    old_bytes: int | None


@dataclass(frozen=True)
class Snapshot:
    """추적 루트 집합의 단일 시점 스냅샷.

    ``entries`` 의 키는 footer 에 그대로 출력되는 display path
    (예: ``.agent/workspace/report.md``). 절대 경로가 아닌 이유는 사용자
    홈 노출을 막고 사고 분석 시 가독성을 위해서이다.
    """

    entries: dict[str, FileEntry]


@dataclass(frozen=True)
class Diff:
    """두 스냅샷 사이의 변경 집합."""

    changes: tuple[FileChange, ...]

    @property
    def is_empty(self) -> bool:
        return not self.changes


@dataclass(frozen=True)
class TrackedRoot:
    """추적할 디렉터리 또는 명시 파일 목록.

    Attributes:
        label: footer 에 쓰이는 표시 prefix (예: ``.agent/workspace``).
        path: 실제 디스크 경로 (절대 경로 권장).
        files: 명시되면 그 파일 이름만 추적한다 (root 직속). ``None`` 이면
            재귀 walk. 페르소나 dir 처럼 dreaming 부산물·SQLite 가 같은
            트리에 있을 때 명시 화이트리스트로 노이즈를 차단한다.
    """

    label: str
    path: Path
    files: tuple[str, ...] | None = None


class FileMutationTracker:
    """워크스페이스/페르소나 파일의 turn-단위 변경을 추적한다.

    사용 패턴:
        tracker = FileMutationTracker([
            TrackedRoot(".agent/workspace", workspace_dir),
            TrackedRoot(".agent", persona_dir,
                        files=("AGENT.md", "USER.md", "MEMORY.md")),
        ])
        before = tracker.snapshot()
        # ... 도구 호출 ...
        after = tracker.snapshot(previous=before)
        diff = tracker.diff(before, after)
        footer = format_footer(diff)
        if footer:
            inject(footer)
    """

    def __init__(
        self,
        roots: list[TrackedRoot],
        *,
        max_files: int = _DEFAULT_MAX_FILES,
        line_count_max_bytes: int = _DEFAULT_LINE_COUNT_MAX_BYTES,
    ) -> None:
        self._roots: tuple[TrackedRoot, ...] = tuple(roots)
        self._max_files = max_files
        self._line_count_max_bytes = line_count_max_bytes

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self, previous: Snapshot | None = None) -> Snapshot:
        """추적 루트의 현재 상태를 캡처한다.

        ``previous`` 가 주어지면 변경되지 않은 파일의 ``line_count`` 를
        재사용하여 stat 비용 외에 추가 read 를 발생시키지 않는다.
        """
        prev_entries = previous.entries if previous is not None else {}
        entries: dict[str, FileEntry] = {}
        truncated = False

        for root in self._roots:
            for display_path, abs_path in self._iter_root(root):
                if len(entries) >= self._max_files:
                    truncated = True
                    break
                entry = self._snapshot_file(
                    abs_path,
                    cached=prev_entries.get(display_path),
                )
                if entry is None:
                    continue
                entries[display_path] = entry
            if truncated:
                break

        if truncated:
            logger.warning(
                "FileMutationTracker: walk truncated at max_files=%d — "
                "추가 변경은 footer 에 반영되지 않을 수 있음",
                self._max_files,
            )

        return Snapshot(entries=entries)

    def _iter_root(self, root: TrackedRoot):
        """단일 root 에서 추적할 (display_path, absolute_path) 쌍을 yield."""
        try:
            base = root.path.expanduser()
        except (RuntimeError, OSError):
            return

        if not base.exists():
            return

        if root.files is not None:
            for name in root.files:
                abs_path = base / name
                if abs_path.is_file():
                    yield f"{root.label}/{name}", abs_path
            return

        # 재귀 walk — os.walk 가 followlinks=False 가 기본이라 symlink loop 안전.
        # ``topdown=True`` 로 ``dirnames`` 를 in-place 수정해 노이즈 디렉터리를
        # 가지치기한다.
        base_str = str(base)
        for dirpath, dirnames, filenames in os.walk(base_str, followlinks=False):
            # in-place 가지치기 — 노이즈 디렉터리는 descend 자체를 차단.
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and d not in _SKIP_DIRNAMES
            ]
            rel_dir = os.path.relpath(dirpath, base_str)
            for filename in filenames:
                if filename in _SKIP_FILENAMES:
                    continue
                if filename.endswith(_SKIP_SUFFIXES):
                    continue
                abs_path = Path(dirpath) / filename
                if rel_dir == ".":
                    rel_path = filename
                else:
                    rel_path = f"{rel_dir}/{filename}"
                # POSIX 구분자로 통일 — Windows 에서도 footer 가 일관되게 보이도록.
                rel_path = rel_path.replace(os.sep, "/")
                yield f"{root.label}/{rel_path}", abs_path

    def _snapshot_file(
        self, abs_path: Path, *, cached: FileEntry | None
    ) -> FileEntry | None:
        """단일 파일의 stat 을 캡처. 변경 없으면 ``cached`` 의 line_count 를
        재사용한다."""
        try:
            st = abs_path.stat()
        except (FileNotFoundError, PermissionError, OSError) as exc:
            logger.debug(
                "FileMutationTracker: stat 실패 %s: %s", abs_path, exc
            )
            return None

        size = int(st.st_size)
        mtime_ns = int(st.st_mtime_ns)

        if (
            cached is not None
            and cached.size == size
            and cached.mtime_ns == mtime_ns
        ):
            return cached

        line_count = self._count_lines(abs_path, size)
        return FileEntry(size=size, mtime_ns=mtime_ns, line_count=line_count)

    def _count_lines(self, abs_path: Path, size: int) -> int | None:
        """파일의 줄 수를 ``\\n`` 카운트로 셈. 큰 파일/실패는 ``None``.

        파일이 ``\\n`` 으로 끝나지 않으면 한 줄 더 있는 것으로 친다 (POSIX
        텍스트 파일 규약과 통상 에디터 출력 양쪽을 자연스럽게 처리).
        """
        if size == 0:
            return 0
        if size > self._line_count_max_bytes:
            return None
        try:
            count = 0
            last_byte = b""
            with open(abs_path, "rb") as fh:
                while True:
                    chunk = fh.read(65536)
                    if not chunk:
                        break
                    count += chunk.count(b"\n")
                    last_byte = chunk[-1:]
            if last_byte and last_byte != b"\n":
                count += 1
            return count
        except (OSError, PermissionError) as exc:
            logger.debug(
                "FileMutationTracker: line_count 실패 %s: %s", abs_path, exc
            )
            return None

    # ------------------------------------------------------------------
    # Diff
    # ------------------------------------------------------------------

    @staticmethod
    def diff(before: Snapshot, after: Snapshot) -> Diff:
        """두 스냅샷의 차이를 안정적인 순서로 반환한다.

        반환 순서: 추가(+) → 수정(M) → 삭제(-). 같은 종류 내에서는
        display_path 알파벳 오름차순. 결정론적 출력은 footer 회귀
        스냅샷 테스트와 LLM 캐시 친화성 양쪽에 도움이 된다.
        """
        before_paths = set(before.entries.keys())
        after_paths = set(after.entries.keys())

        added = sorted(after_paths - before_paths)
        deleted = sorted(before_paths - after_paths)
        common = before_paths & after_paths

        modified: list[str] = []
        for path in sorted(common):
            b = before.entries[path]
            a = after.entries[path]
            if b.size != a.size or b.mtime_ns != a.mtime_ns:
                modified.append(path)

        changes: list[FileChange] = []
        for path in added:
            e = after.entries[path]
            changes.append(FileChange(
                display_path=path,
                kind="added",
                new_lines=e.line_count,
                new_bytes=e.size,
                old_lines=None,
                old_bytes=None,
            ))
        for path in modified:
            b = before.entries[path]
            a = after.entries[path]
            changes.append(FileChange(
                display_path=path,
                kind="modified",
                new_lines=a.line_count,
                new_bytes=a.size,
                old_lines=b.line_count,
                old_bytes=b.size,
            ))
        for path in deleted:
            b = before.entries[path]
            changes.append(FileChange(
                display_path=path,
                kind="deleted",
                new_lines=None,
                new_bytes=None,
                old_lines=b.line_count,
                old_bytes=b.size,
            ))

        return Diff(changes=tuple(changes))


def format_footer(diff: Diff) -> str:
    """``Diff`` 를 ReAct Observation footer 로 렌더한다.

    변경이 없으면 빈 문자열을 반환한다 (토큰 절약).
    """
    if diff.is_empty:
        return ""

    lines = ["[file changes this turn]"]
    for change in diff.changes:
        if change.kind == "added":
            qty = _format_size(change.new_lines, change.new_bytes)
            lines.append(f"+ {change.display_path} ({qty})")
        elif change.kind == "modified":
            new_qty = _format_size(change.new_lines, change.new_bytes)
            old_qty = _format_size(change.old_lines, change.old_bytes)
            if new_qty == old_qty:
                # 라인 카운트가 둘 다 ``None`` (=bytes 표시) 인데 byte 표현
                # 이 같다면 mtime 만 바뀐 노이즈일 수 있으니 그렇게 명시.
                lines.append(
                    f"M {change.display_path} (touched, {new_qty})"
                )
            else:
                lines.append(
                    f"M {change.display_path} ({old_qty} → {new_qty})"
                )
        elif change.kind == "deleted":
            old_qty = _format_size(change.old_lines, change.old_bytes)
            lines.append(f"- {change.display_path} (was {old_qty})")
    return "\n".join(lines)


def _format_size(line_count: int | None, byte_count: int | None) -> str:
    """``(N lines)`` 또는 ``(N bytes)`` 형식 문자열로 변환."""
    if line_count is not None:
        return f"{line_count} lines"
    if byte_count is not None:
        return f"{byte_count} bytes"
    return "unknown size"
