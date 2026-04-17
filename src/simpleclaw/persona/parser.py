"""Markdown persona file parser."""

from __future__ import annotations

import logging
from pathlib import Path

from markdown_it import MarkdownIt

from simpleclaw.persona.models import FileType, PersonaFile, Section, SourceScope

logger = logging.getLogger(__name__)


def parse_markdown(
    file_path: str | Path,
    file_type: FileType,
    source_scope: SourceScope = SourceScope.LOCAL,
) -> PersonaFile:
    """Parse a single markdown file into a PersonaFile.

    Returns a PersonaFile with empty sections if the file is missing,
    empty, or cannot be decoded.
    """
    file_path = Path(file_path)

    if not file_path.is_file():
        logger.warning("Persona file not found: %s", file_path)
        return PersonaFile(
            file_type=file_type,
            source_path=str(file_path),
            source_scope=source_scope,
        )

    try:
        raw_content = file_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as e:
        logger.warning("Failed to read persona file %s: %s", file_path, e)
        return PersonaFile(
            file_type=file_type,
            source_path=str(file_path),
            source_scope=source_scope,
        )

    if not raw_content.strip():
        return PersonaFile(
            file_type=file_type,
            source_path=str(file_path),
            source_scope=source_scope,
            raw_content=raw_content,
        )

    sections = _extract_sections(raw_content)

    return PersonaFile(
        file_type=file_type,
        source_path=str(file_path),
        source_scope=source_scope,
        sections=sections,
        raw_content=raw_content,
    )


def _extract_sections(text: str) -> list[Section]:
    """Extract sections from markdown text using heading boundaries."""
    md = MarkdownIt()
    tokens = md.parse(text)

    headings: list[tuple[int, str, int]] = []  # (level, title, line_index)
    lines = text.split("\n")

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.type == "heading_open":
            level = int(token.tag[1])  # h1 -> 1, h2 -> 2, etc.
            line = token.map[0] if token.map else 0
            # Next token is the inline content
            if i + 1 < len(tokens) and tokens[i + 1].type == "inline":
                title = tokens[i + 1].content
            else:
                title = ""
            headings.append((level, title, line))
        i += 1

    if not headings:
        # No headings — treat entire content as a single untitled section
        return [Section(level=0, title="", content=text.strip())]

    sections: list[Section] = []

    # Content before first heading (if any)
    first_heading_line = headings[0][2]
    preamble = "\n".join(lines[:first_heading_line]).strip()
    if preamble:
        sections.append(Section(level=0, title="", content=preamble))

    for idx, (level, title, start_line) in enumerate(headings):
        # Content starts after the heading line
        content_start = start_line + 1
        if idx + 1 < len(headings):
            content_end = headings[idx + 1][2]
        else:
            content_end = len(lines)

        content = "\n".join(lines[content_start:content_end]).strip()
        sections.append(Section(level=level, title=title, content=content))

    return sections
