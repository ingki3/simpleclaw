"""Study wiki 페이지(Markdown)에 수집 결과를 *부분 병합*하는 updater.

설계 의도 — "통째로 덮어쓰지 않는다":
    study runner 가 매일 새로 공부한 내용을 wiki 페이지에 반영할 때, 페이지를
    통째로 재생성하면 두 가지가 깨진다. (1) 운영자가 손으로 추가한 메모/판단이
    날아가고, (2) 누적돼야 할 "역사적 맥락"이 매번 초기화된다. 그래서 이 모듈은
    페이지를 **관리(managed) 섹션**과 **수동(manual) 섹션**으로 나눠, 관리 섹션만
    갱신하고 그 외 모든 섹션은 원문 위치 그대로 보존한다.

관리하는 섹션(이 목록 밖은 전부 수동 섹션으로 간주해 보존):
    - "현재 상태"          — 주제의 현재 스냅샷
    - "최근 업데이트"      — 날짜순 누적 변경 로그(새 항목을 위로 prepend)
    - "역사적/사회적 맥락" — 배경 지식(있으면 교체)
    - "형님 관심사와의 연결" — 사용자 관심사와의 연결(있으면 교체)
    - "답변 시 주의사항"   — 응답에 쓸 때의 면책/주의(있으면 교체)
    - "확인 필요"          — 저신뢰/미검증 항목을 따로 격리
    - "Sources"            — 출처 URL 목록(누적 dedup)

저신뢰 처리:
    confidence 가 ``low`` 이면 새 업데이트를 "최근 업데이트"가 아니라 "확인 필요"로
    보낸다 — 검증되지 않은 외부 사실을 "확정 사실"처럼 노출하지 않기 위함. 동시에
    "답변 시 주의사항"에 면책 문구를 남긴다. (설계 문서
    ``docs/agent-study-wiki.md`` 의 safety 정책과 일치.)
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

# 관리 섹션의 정규 순서. 원문에 없던 관리 섹션을 새로 추가할 때 이 순서로 덧붙인다.
SECTION_CURRENT_STATE = "현재 상태"
SECTION_RECENT_UPDATES = "최근 업데이트"
SECTION_CONTEXT = "역사적/사회적 맥락"
SECTION_INTEREST_LINK = "형님 관심사와의 연결"
SECTION_CAUTIONS = "답변 시 주의사항"
SECTION_NEEDS_CHECK = "확인 필요"
SECTION_SOURCES = "Sources"

CANONICAL_SECTION_ORDER: tuple[str, ...] = (
    SECTION_CURRENT_STATE,
    SECTION_RECENT_UPDATES,
    SECTION_CONTEXT,
    SECTION_INTEREST_LINK,
    SECTION_CAUTIONS,
    SECTION_NEEDS_CHECK,
    SECTION_SOURCES,
)
MANAGED_SECTIONS: frozenset[str] = frozenset(CANONICAL_SECTION_ORDER)

# 저신뢰 항목을 응답에 쓸 때 함께 노출할 기본 면책 문구.
LOW_CONFIDENCE_DISCLAIMER = (
    "저신뢰 출처가 포함됨 — 확정 사실이 아닌 *보도/추정*으로만 인용할 것."
)

# "최근 업데이트" 로그가 무한히 길어지지 않도록 한 페이지에 보존할 최대 항목 수.
DEFAULT_MAX_RECENT_ITEMS = 20

# 레벨2 섹션 헤더(``## 제목``) 매칭. 코드 블록 안의 ``##`` 는 study 페이지에서
# 다루지 않으므로 단순 행 기준 매칭으로 충분하다.
_H2_RE = re.compile(r"^##\s+(.*\S)\s*$")
_H1_RE = re.compile(r"^#\s+(.*\S)\s*$")


@dataclass
class _Section:
    """레벨2 섹션 하나 — 제목과 본문 라인들(헤더 라인 제외)."""

    title: str
    lines: list[str] = field(default_factory=list)

    @property
    def is_managed(self) -> bool:
        return self.title in MANAGED_SECTIONS

    def body_text(self) -> str:
        """앞뒤 공백을 정리한 본문 텍스트."""
        return "\n".join(self.lines).strip()

    def bullet_items(self) -> list[str]:
        """본문에서 ``- `` 불릿 항목만 추출(순서 보존)."""
        items: list[str] = []
        for line in self.lines:
            stripped = line.strip()
            if stripped.startswith("- "):
                items.append(stripped[2:].strip())
        return items


def _parse_page(text: str) -> tuple[list[str], list[_Section]]:
    """페이지를 (preamble, sections) 로 분해한다.

    preamble 은 첫 ``## `` 이전의 모든 라인(보통 ``# 제목`` 과 도입부). sections 는
    등장 순서를 보존한 레벨2 섹션 목록. 원문 순서 보존이 "수동 섹션을 제자리에
    둔다"는 핵심 불변식의 근거다.
    """
    preamble: list[str] = []
    sections: list[_Section] = []
    current: _Section | None = None
    for line in text.splitlines():
        match = _H2_RE.match(line)
        if match:
            current = _Section(title=match.group(1).strip())
            sections.append(current)
        elif current is None:
            preamble.append(line)
        else:
            current.lines.append(line)
    return preamble, sections


def _ensure_title(preamble: list[str], topic_title: str) -> list[str]:
    """preamble 에 H1 제목이 없으면 추가한다.

    이미 H1 이 있으면 운영자가 손본 제목일 수 있으므로 건드리지 않는다(수동
    권위 존중). 없을 때만 ``# {topic_title}`` 을 맨 앞에 넣는다.
    """
    has_h1 = any(_H1_RE.match(line) for line in preamble)
    if has_h1:
        return [*preamble]
    title_block = [f"# {topic_title}", ""]
    # 기존 preamble 이 공백뿐이면 제목만 남긴다.
    if any(line.strip() for line in preamble):
        return [*title_block, *preamble]
    return title_block


def _render_section(title: str, lines: Sequence[str]) -> list[str]:
    """``## 제목`` + 본문 라인을 렌더한다(본문 없으면 헤더만)."""
    body = [line for line in lines]
    # 본문 앞뒤 공백 라인 정리.
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    return [f"## {title}", *body]


def _bullets(items: Sequence[str]) -> list[str]:
    """문자열 목록을 ``- 항목`` 불릿 라인으로 변환(빈 항목 제외)."""
    return [f"- {item.strip()}" for item in items if item and item.strip()]


def _format_update_line(text: str, timestamp: str | None) -> str:
    """"최근 업데이트" 한 줄을 ``- [날짜] 내용`` 형태로 만든다."""
    clean = text.strip()
    if timestamp:
        return f"{timestamp} — {clean}"
    return clean


def _dedup_keep_order(items: Sequence[str]) -> list[str]:
    """순서를 보존하며 중복을 제거한다."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def merge_study_update(
    existing: str,
    *,
    topic_title: str,
    updates: Sequence[str] = (),
    sources: Sequence[str] = (),
    current_state: str | None = None,
    historical_context: str | None = None,
    interest_link: str | None = None,
    cautions: Sequence[str] = (),
    needs_check: Sequence[str] = (),
    confidence: str = "medium",
    timestamp: str | None = None,
    max_recent_items: int = DEFAULT_MAX_RECENT_ITEMS,
) -> str:
    """study 수집 결과를 기존 wiki 페이지에 부분 병합한다.

    페이지를 통째로 덮어쓰지 않고 *관리 섹션*만 갱신한다. 관리 목록 밖의 모든
    섹션(예: 운영자 수동 메모)은 원문 위치 그대로 보존된다.

    Args:
        existing: 기존 페이지 Markdown(빈 문자열이면 새 페이지로 생성).
        topic_title: 주제 제목. 페이지에 H1 이 없을 때만 ``# {title}`` 로 추가.
        updates: 이번에 공부한 변경/사실 항목들. confidence 가 ``low`` 가 아니면
            "최근 업데이트"에 최신 항목으로 prepend 된다.
        sources: 출처 URL 목록. "Sources" 섹션에 dedup 누적된다.
        current_state: 주어지면 "현재 상태" 섹션 본문을 교체.
        historical_context: 주어지면 "역사적/사회적 맥락" 섹션 본문을 교체.
        interest_link: 주어지면 "형님 관심사와의 연결" 섹션 본문을 교체.
        cautions: 주어지면 "답변 시 주의사항" 섹션을 이 불릿들로 교체.
        needs_check: "확인 필요" 섹션에 prepend 할 미검증 항목들.
        confidence: ``"high" | "medium" | "low"``. ``low`` 면 updates 를 "최근
            업데이트" 대신 "확인 필요"로 보내고 주의사항에 면책 문구를 추가한다.
        timestamp: 업데이트 항목에 붙일 날짜/시각 문자열(ISO 등). None 이면 생략.
        max_recent_items: "최근 업데이트"에 보존할 최대 항목 수.

    Returns:
        병합된 페이지 Markdown 문자열(끝에 개행 1개).
    """
    preamble, sections = _parse_page(existing or "")
    preamble = _ensure_title(preamble, topic_title)

    by_title: dict[str, _Section] = {}
    for section in sections:
        # 같은 제목의 관리 섹션이 중복되면 첫 번째만 갱신 대상으로 본다.
        by_title.setdefault(section.title, section)

    is_low = confidence.strip().lower() == "low"

    # 저신뢰면 업데이트를 "확인 필요"로 우회시킨다(확정 사실로 노출 금지).
    recent_new = list(updates) if not is_low else []
    needs_check_new = list(needs_check) + (list(updates) if is_low else [])

    # 관리 섹션별로 "새 본문"을 계산한다. 값이 있는 항목만 실제로 갱신/생성한다.
    new_bodies: dict[str, list[str]] = {}

    # 현재 상태 / 맥락 / 관심사 연결 — 주어지면 본문 교체.
    if current_state is not None:
        new_bodies[SECTION_CURRENT_STATE] = current_state.strip().splitlines()
    if historical_context is not None:
        new_bodies[SECTION_CONTEXT] = historical_context.strip().splitlines()
    if interest_link is not None:
        new_bodies[SECTION_INTEREST_LINK] = interest_link.strip().splitlines()

    # 최근 업데이트 — 새 항목을 위로, 기존 항목을 아래로 누적(cap 적용).
    if recent_new:
        existing_recent = (
            by_title[SECTION_RECENT_UPDATES].bullet_items()
            if SECTION_RECENT_UPDATES in by_title
            else []
        )
        formatted_new = [_format_update_line(u, timestamp) for u in recent_new]
        merged = _dedup_keep_order([*formatted_new, *existing_recent])
        new_bodies[SECTION_RECENT_UPDATES] = _bullets(merged[:max_recent_items])

    # 확인 필요 — 저신뢰/미검증 항목 누적.
    if needs_check_new:
        existing_check = (
            by_title[SECTION_NEEDS_CHECK].bullet_items()
            if SECTION_NEEDS_CHECK in by_title
            else []
        )
        merged = _dedup_keep_order([*needs_check_new, *existing_check])
        new_bodies[SECTION_NEEDS_CHECK] = _bullets(merged)

    # 답변 시 주의사항 — 명시 cautions + 저신뢰 면책.
    caution_items = list(cautions)
    if is_low and (updates or needs_check):
        caution_items = [LOW_CONFIDENCE_DISCLAIMER, *caution_items]
    if caution_items:
        existing_cautions = (
            by_title[SECTION_CAUTIONS].bullet_items()
            if SECTION_CAUTIONS in by_title
            else []
        )
        merged = _dedup_keep_order([*caution_items, *existing_cautions])
        new_bodies[SECTION_CAUTIONS] = _bullets(merged)

    # Sources — URL dedup 누적.
    if sources:
        existing_sources = (
            by_title[SECTION_SOURCES].bullet_items()
            if SECTION_SOURCES in by_title
            else []
        )
        merged = _dedup_keep_order([*existing_sources, *sources])
        new_bodies[SECTION_SOURCES] = _bullets(merged)

    # 재조립: preamble → 기존 섹션(순서 보존, 관리 섹션만 갱신) → 신규 관리 섹션.
    out_blocks: list[list[str]] = []

    head = [*preamble]
    while head and not head[-1].strip():
        head.pop()
    if head:
        out_blocks.append(head)

    handled: set[str] = set()
    for section in sections:
        title = section.title
        if title in by_title and section is not by_title[title]:
            # 중복 제목의 2번째 이후는 수동 콘텐츠로 간주해 원문대로 보존.
            out_blocks.append(_render_section(title, section.lines))
            continue
        if title in new_bodies:
            out_blocks.append(_render_section(title, new_bodies[title]))
            handled.add(title)
        else:
            out_blocks.append(_render_section(title, section.lines))

    # 원문에 없던 관리 섹션을 정규 순서로 추가.
    for title in CANONICAL_SECTION_ORDER:
        if title in new_bodies and title not in handled and title not in by_title:
            out_blocks.append(_render_section(title, new_bodies[title]))
            handled.add(title)

    body = "\n\n".join("\n".join(block) for block in out_blocks).strip()
    return body + "\n"


def merge_open_questions(existing: str, questions: Sequence[str]) -> str:
    """``open_questions.md`` 에 미해결 질문을 dedup 누적한다.

    저신뢰 출처라서 wiki 본문에 단정적으로 쓸 수 없는 항목을 별도 파일에 모아
    후속 검증 대상으로 남긴다(설계 문서의 저신뢰 처리 정책).

    Args:
        existing: 기존 open_questions.md 내용(없으면 빈 문자열).
        questions: 추가할 질문/미검증 항목들.

    Returns:
        갱신된 Markdown(끝에 개행 1개).
    """
    header = "# Open Questions"
    # 헤더/섹션 구조와 무관하게 모든 ``- `` 불릿을 기존 항목으로 본다(단순/견고).
    existing_items = [
        line.strip()[2:].strip()
        for line in (existing or "").splitlines()
        if line.strip().startswith("- ")
    ]
    merged = _dedup_keep_order([*existing_items, *questions])
    lines = [header, "", *_bullets(merged)]
    return "\n".join(lines).strip() + "\n"
