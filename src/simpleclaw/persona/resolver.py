"""Persona file path resolver with local/global priority."""

from __future__ import annotations

import logging
from pathlib import Path

from simpleclaw.persona.models import FileType, PersonaFile, SourceScope
from simpleclaw.persona.parser import parse_markdown

logger = logging.getLogger(__name__)

_FILE_MAP = {
    "AGENT.md": FileType.AGENT,
    "USER.md": FileType.USER,
    "MEMORY.md": FileType.MEMORY,
}


def resolve_persona_files(
    local_dir: str | Path,
    global_dir: str | Path,
) -> list[PersonaFile]:
    """Resolve persona files from local and global directories.

    Local files take priority over global files for the same file type.
    Returns a list of PersonaFile objects for all found files.
    """
    local_path = Path(local_dir).expanduser()
    global_path = Path(global_dir).expanduser()

    resolved: dict[FileType, PersonaFile] = {}

    # Scan global first (lower priority)
    _scan_directory(global_path, SourceScope.GLOBAL, resolved)

    # Scan local second (higher priority — overwrites global)
    _scan_directory(local_path, SourceScope.LOCAL, resolved)

    # Return in canonical order
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
    """Scan a directory for persona files and add them to resolved."""
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
