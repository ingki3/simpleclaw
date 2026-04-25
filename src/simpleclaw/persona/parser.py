"""마크다운 페르소나 파일 파서.

마크다운 형식의 페르소나 파일을 읽어 구조화된 PersonaFile 객체로 변환한다.
markdown-it-py 라이브러리를 사용하여 헤딩 경계 기준으로 섹션을 추출한다.

동작 흐름:
1. 파일 존재 여부·읽기 가능 여부 확인
2. markdown-it로 토큰 파싱하여 헤딩 위치 수집
3. 헤딩 간 텍스트를 Section 객체로 분할
"""

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
    """단일 마크다운 파일을 파싱하여 PersonaFile로 변환한다.

    파일이 존재하지 않거나, 비어 있거나, 디코딩에 실패하면
    섹션이 빈 PersonaFile을 반환한다 (에러를 발생시키지 않음).

    Args:
        file_path: 마크다운 파일 경로.
        file_type: 파일 유형 (AGENT / USER / MEMORY).
        source_scope: 로드 출처 (LOCAL / GLOBAL).

    Returns:
        파싱된 PersonaFile 객체.
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
    """마크다운 텍스트에서 헤딩 경계를 기준으로 섹션을 추출한다.

    헤딩이 없으면 전체 텍스트를 제목 없는 단일 섹션으로 반환한다.
    첫 헤딩 이전에 텍스트가 있으면 프리앰블 섹션(level=0)으로 추가한다.

    Args:
        text: 원본 마크다운 텍스트.

    Returns:
        추출된 Section 객체 리스트.
    """
    md = MarkdownIt()
    tokens = md.parse(text)

    headings: list[tuple[int, str, int]] = []  # (헤딩 수준, 제목, 줄 번호)
    lines = text.split("\n")

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.type == "heading_open":
            level = int(token.tag[1])  # h1 → 1, h2 → 2 등
            line = token.map[0] if token.map else 0
            # 다음 토큰이 인라인 콘텐츠(헤딩 텍스트)
            if i + 1 < len(tokens) and tokens[i + 1].type == "inline":
                title = tokens[i + 1].content
            else:
                title = ""
            headings.append((level, title, line))
        i += 1

    if not headings:
        # 헤딩 없음 — 전체 내용을 제목 없는 단일 섹션으로 처리
        return [Section(level=0, title="", content=text.strip())]

    sections: list[Section] = []

    # 첫 헤딩 이전 내용(프리앰블)이 있으면 별도 섹션으로 추가
    first_heading_line = headings[0][2]
    preamble = "\n".join(lines[:first_heading_line]).strip()
    if preamble:
        sections.append(Section(level=0, title="", content=preamble))

    for idx, (level, title, start_line) in enumerate(headings):
        # 본문은 헤딩 줄 다음부터 시작
        content_start = start_line + 1
        if idx + 1 < len(headings):
            content_end = headings[idx + 1][2]
        else:
            content_end = len(lines)

        content = "\n".join(lines[content_start:content_end]).strip()
        sections.append(Section(level=level, title=title, content=content))

    return sections
