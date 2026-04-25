"""페르소나 파일 경로 리졸버 — 로컬/글로벌 우선순위 처리.

로컬 디렉터리와 글로벌 디렉터리에서 페르소나 파일(AGENT.md, USER.md, MEMORY.md)을
탐색하고, 동일 유형의 파일이 양쪽에 있으면 로컬 파일이 우선한다.

설계 결정:
- 글로벌 디렉터리를 먼저 스캔한 뒤 로컬을 스캔하여 덮어쓰는 방식으로
  우선순위를 구현한다 (후발 우선).
- 결과는 AGENT → USER → MEMORY 정규 순서로 반환한다.
"""

from __future__ import annotations

import logging
from pathlib import Path

from simpleclaw.persona.models import FileType, PersonaFile, SourceScope
from simpleclaw.persona.parser import parse_markdown

logger = logging.getLogger(__name__)

# 파일명 → FileType 매핑 (탐색 대상 정의)
_FILE_MAP = {
    "AGENT.md": FileType.AGENT,
    "USER.md": FileType.USER,
    "MEMORY.md": FileType.MEMORY,
}


def resolve_persona_files(
    local_dir: str | Path,
    global_dir: str | Path,
) -> list[PersonaFile]:
    """로컬 및 글로벌 디렉터리에서 페르소나 파일을 탐색·해석한다.

    동일 유형의 파일이 양쪽에 있으면 로컬 파일이 우선한다.
    결과는 AGENT → USER → MEMORY 정규 순서로 반환된다.

    Args:
        local_dir: 프로젝트 로컬 페르소나 디렉터리 경로.
        global_dir: 사용자 전역 페르소나 디렉터리 경로.

    Returns:
        탐색된 PersonaFile 객체 리스트 (정규 순서).
    """
    local_path = Path(local_dir).expanduser()
    global_path = Path(global_dir).expanduser()

    resolved: dict[FileType, PersonaFile] = {}

    # 글로벌 먼저 스캔 (낮은 우선순위)
    _scan_directory(global_path, SourceScope.GLOBAL, resolved)

    # 로컬을 나중에 스캔 (높은 우선순위 — 글로벌 덮어씀)
    _scan_directory(local_path, SourceScope.LOCAL, resolved)

    # 정규 순서로 반환
    result = []
    for ft in [FileType.AGENT, FileType.USER, FileType.MEMORY]:
        if ft in resolved:
            result.append(resolved[ft])
        else:
            logger.warning("Persona file not found for type: %s", ft.value)

    return result


def _scan_directory(
    directory: Path,
    scope: SourceScope,
    resolved: dict[FileType, PersonaFile],
) -> None:
    """디렉터리에서 페르소나 파일을 탐색하여 resolved 딕셔너리에 추가한다.

    Args:
        directory: 탐색할 디렉터리 경로.
        scope: 출처 범위 (LOCAL / GLOBAL).
        resolved: FileType → PersonaFile 매핑 (호출자가 전달, 제자리 갱신).
    """
    if not directory.is_dir():
        logger.debug("Persona directory does not exist: %s", directory)
        return

    for filename, file_type in _FILE_MAP.items():
        file_path = directory / filename
        if file_path.is_file():
            persona = parse_markdown(file_path, file_type, scope)
            if scope == SourceScope.LOCAL and file_type in resolved:
                logger.info(
                    "Local override: %s replaces global %s",
                    file_path,
                    resolved[file_type].source_path,
                )
            resolved[file_type] = persona
