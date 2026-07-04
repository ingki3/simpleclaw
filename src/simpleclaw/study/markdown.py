"""``StudyPage`` ↔ Markdown 왕복 직렬화.

위키의 source of truth 는 사람이 읽는 Markdown 이다. 따라서 직렬화 포맷은
운영자가 에디터로 열어 한 줄씩 고칠 수 있을 만큼 단순해야 한다. 포맷은 다음과
같다.

    ---
    topic_id: ai-industry-openai
    title: OpenAI
    updated_at: 2026-06-29T06:30:00+09:00
    ---

    # OpenAI

    {요약 문단}

    ## 현재 상태
    - ...

    ## 역사적/사회적 맥락
    - ...

    ## 형님 관심사와의 연결
    - ...

    ## 답변 시 주의사항
    - ...

    ## 열린 질문
    - ...

    ## Sources
    - 2026-06-26 — 매일경제 — https://... (confidence: 0.80)

설계 결정 — frontmatter 는 최소화:
    YAML frontmatter 에는 식별/정렬에 꼭 필요한 ``StudyPage`` 필드(topic_id,
    title, updated_at)만 둔다. 본문 섹션과 출처는 사람이 직접 고칠 수 있는
    Markdown 으로 두어, 한쪽(frontmatter)과 다른 쪽(본문)이 같은 사실을 중복
    보관하다 어긋나는 것을 피한다.

설계 결정 — 알 수 없는 섹션 보존하지 않음(MVP):
    파서는 알려진 한국어 섹션 제목만 해당 필드로 매핑한다. MVP 범위에서는
    임의 섹션 보존을 지원하지 않으며, 이는 후속 이슈에서 필요 시 확장한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .types import StudyPage, StudySource

# Markdown 섹션 제목 ↔ StudyPage 필드 매핑(렌더 순서 = 정의 순서).
# 한국어 제목을 정규 키로 쓰고, 파싱 시 동일 제목을 역매핑한다.
_SECTION_FIELDS: list[tuple[str, str]] = [
    ("현재 상태", "current_state"),
    ("역사적/사회적 맥락", "historical_context"),
    ("형님 관심사와의 연결", "personal_relevance"),
    ("답변 시 주의사항", "answer_guidance"),
    ("열린 질문", "open_questions"),
]

_SOURCES_HEADING = "Sources"

# "- 텍스트" 형태의 불릿 한 줄을 잡는다(앞 공백 허용).
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")

# 출처 줄 끝의 "(confidence: 0.80)" 접미사.
_CONFIDENCE_RE = re.compile(r"\s*\(confidence:\s*([0-9]*\.?[0-9]+)\)\s*$")

# URL 토큰(http/https).
_URL_RE = re.compile(r"https?://\S+")


def _render_bullets(items: list[str]) -> str:
    """문자열 목록을 Markdown 불릿 블록으로 만든다(빈 목록이면 빈 문자열)."""
    return "\n".join(f"- {item}" for item in items if item is not None)


def _render_source(source: StudySource) -> str:
    """``StudySource`` 한 건을 사람이 읽는 불릿 한 줄로 직렬화한다.

    형식: ``- [{published_at} — ]{title} — {url}[  (confidence: x.xx)]``
    발행일·신뢰도는 값이 있을 때만 덧붙여 잡음을 줄인다.
    """
    parts: list[str] = []
    if source.published_at:
        parts.append(source.published_at)
    parts.append(source.title)
    parts.append(source.url)
    line = "- " + " — ".join(parts)
    if source.confidence:
        line += f"  (confidence: {source.confidence:.2f})"
    return line


def render_study_page(page: StudyPage) -> str:
    """``StudyPage`` 를 Markdown 텍스트로 직렬화한다.

    Args:
        page: 직렬화할 페이지.

    Returns:
        frontmatter + 본문을 합친 Markdown 문자열(끝에 개행 포함).
    """
    # frontmatter 는 식별/정렬용 필드만. None 값은 생략해 깔끔하게 유지한다.
    front: dict[str, str] = {"topic_id": page.topic_id, "title": page.title}
    if page.updated_at:
        front["updated_at"] = page.updated_at
    # sort_keys=False 로 위에서 넣은 순서를 보존한다.
    front_text = yaml.safe_dump(front, allow_unicode=True, sort_keys=False).rstrip("\n")

    blocks: list[str] = [f"---\n{front_text}\n---", f"# {page.title}"]

    if page.summary.strip():
        blocks.append(page.summary.strip())

    for heading, attr in _SECTION_FIELDS:
        items: list[str] = getattr(page, attr)
        body = _render_bullets(items)
        # 빈 섹션도 제목은 남겨 사람이 채워 넣을 자리를 보이게 한다.
        blocks.append(f"## {heading}\n{body}".rstrip())

    source_lines = "\n".join(_render_source(s) for s in page.sources)
    blocks.append(f"## {_SOURCES_HEADING}\n{source_lines}".rstrip())

    return "\n\n".join(blocks) + "\n"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """선행 YAML frontmatter 를 (dict, 남은 본문) 으로 분리한다.

    frontmatter 가 없으면 ``({}, text)`` 를 반환한다.
    """
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    if not match:
        return {}, text
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return data, text[match.end():]


def _split_sections(body: str) -> tuple[str, dict[str, list[str]]]:
    """본문을 (H1 아래 요약 문단, {섹션제목: 불릿목록}) 으로 분해한다.

    ``## 제목`` 을 경계로 섹션을 나눈다. H1(``# ...``) 과 그 아래 첫 ``##`` 사이의
    일반 텍스트는 요약으로 본다.
    """
    summary_lines: list[str] = []
    sections: dict[str, list[str]] = {}
    current: str | None = None  # 현재 섹션 제목(없으면 요약 영역)

    for line in body.splitlines():
        h1 = re.match(r"^#\s+(.*)$", line)
        h2 = re.match(r"^##\s+(.*)$", line)
        if h2:
            current = h2.group(1).strip()
            sections.setdefault(current, [])
            continue
        if h1:
            # H1 은 제목(frontmatter title 과 동일)이라 본문에서는 버린다.
            current = None
            continue
        if current is None:
            summary_lines.append(line)
        else:
            bullet = _BULLET_RE.match(line)
            if bullet:
                sections[current].append(bullet.group(1).strip())
            # 불릿이 아닌 줄(빈 줄 등)은 무시한다 — MVP 는 불릿 목록만 다룬다.

    return "\n".join(summary_lines).strip(), sections


def _parse_source(line: str) -> StudySource | None:
    """출처 불릿 한 줄을 ``StudySource`` 로 역직렬화한다.

    ``_render_source`` 의 역연산. URL 이 없으면 출처로 보지 않고 ``None`` 반환.
    """
    confidence = 0.0
    conf_match = _CONFIDENCE_RE.search(line)
    if conf_match:
        confidence = float(conf_match.group(1))
        line = line[: conf_match.start()]

    url_match = _URL_RE.search(line)
    if not url_match:
        return None
    url = url_match.group(0).strip()

    # URL 토큰을 떼어낸 뒤 남는 부분을 " — " 로 쪼개 [날짜?, 제목] 을 얻는다.
    remainder = (line[: url_match.start()] + line[url_match.end():]).strip()
    remainder = remainder.strip(" —-")
    segments = [s.strip() for s in remainder.split(" — ") if s.strip()]

    published_at: str | None = None
    title = url  # 제목 누락 시 URL 로 대체
    if len(segments) >= 2:
        published_at, title = segments[0], " — ".join(segments[1:])
    elif len(segments) == 1:
        title = segments[0]

    return StudySource(
        title=title,
        url=url,
        published_at=published_at,
        confidence=confidence,
    )


def parse_study_page(text: str, path: str | Path) -> StudyPage:
    """Markdown 텍스트를 ``StudyPage`` 로 역직렬화한다.

    :func:`render_study_page` 의 역연산이며, 사람이 손으로 편집한 페이지도 최대한
    관대하게 해석한다(누락 섹션/필드는 기본값으로 채움).

    Args:
        text: 페이지 Markdown 전체.
        path: 이 페이지의 파일 경로(``StudyPage.path`` 로 보존).

    Returns:
        파싱된 ``StudyPage``.
    """
    front, body = _parse_frontmatter(text)
    summary, sections = _split_sections(body)

    # title: frontmatter 우선, 없으면 본문 H1 에서 회수.
    title = str(front.get("title") or "").strip()
    if not title:
        h1 = re.search(r"^#\s+(.*)$", body, re.MULTILINE)
        title = h1.group(1).strip() if h1 else ""

    topic_id = str(front.get("topic_id") or "").strip()
    updated_at = front.get("updated_at")
    updated_at = str(updated_at) if updated_at else None

    kwargs: dict[str, list[str]] = {}
    for heading, attr in _SECTION_FIELDS:
        kwargs[attr] = list(sections.get(heading, []))

    sources: list[StudySource] = []
    for line in sections.get(_SOURCES_HEADING, []):
        parsed = _parse_source(line)
        if parsed is not None:
            sources.append(parsed)

    return StudyPage(
        topic_id=topic_id,
        path=Path(path),
        title=title,
        summary=summary,
        sources=sources,
        updated_at=updated_at,
        **kwargs,
    )
