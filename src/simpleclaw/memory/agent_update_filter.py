"""AGENT.md dreaming 업데이트 필터.

AGENT.md의 managed:dreaming:dreaming-updates 섹션은 앞으로도 유효한 행동 규칙과
운영 정책만 담아야 한다. 이 모듈은 LLM이 반환한 bullet을 저장 직전에 한 번 더
검증해, MEMORY.md가 맡아야 할 사건 기록/완료 로그가 AGENT.md에 누적되지 않게 한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

_BULLET_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")
_NON_WORD_RE = re.compile(r"[^0-9A-Za-z가-힣_]+")
_DATE_RE = re.compile(r"\b\d{4}[-./년]\s*\d{1,2}[-./월]?\s*\d{0,2}\s*일?\b")

# 앞으로도 적용되는 규칙임을 직접 드러내는 표현. 이 신호가 있으면 recipe/cron 같은
# 단어가 있어도 정책 후보로 본다. 중복 검사는 별도로 수행한다.
_POLICY_HINTS = (
    "앞으로",
    "항상",
    "반드시",
    "기본적으로",
    "원칙",
    "규칙",
    "정책",
    "운영 정책",
    "해야 한다",
    "하지 않는다",
    "때는",
    "경우에는",
    "만들 때",
    "생성할 때",
    "수정할 때",
)

_EVENT_OBJECT_HINTS = (
    "레시피",
    "recipe",
    "크론",
    "cron",
    "link-to-wiki",
    "check_new_emails",
    "usstock-night",
)
_EVENT_ACTION_HINTS = (
    "생성함",
    "생성했다",
    "생성 완료",
    "추가함",
    "추가했다",
    "추가 완료",
    "수정함",
    "수정했다",
    "수정 완료",
    "변경함",
    "변경했다",
    "변경 완료",
    "조정했다",
    "조정함",
    "삭제함",
    "삭제했다",
    "완료",
    "배포함",
    "배포했다",
)
_MEMORY_STORY_HINTS = (
    "운영자가",
    "사용자가",
    "확인됨",
    "확인됐다",
    "작업했다",
    "진행했다",
)


@dataclass(frozen=True)
class AgentUpdateFilterResult:
    """AGENT update 필터링 결과.

    Attributes:
        text: 저장해도 되는 bullet만 다시 합친 본문.
        kept: 보존된 원본 bullet 목록.
        dropped: 제거된 원본 bullet 목록.
    """

    text: str
    kept: list[str]
    dropped: list[str]


def _split_bullets(text: str) -> list[str]:
    """자유 텍스트를 bullet 단위로 나눈다.

    LLM 출력은 대부분 ``- ...`` 형태지만, 방어적으로 bullet prefix가 없는 여러 줄도
    개별 항목으로 취급한다. 빈 줄은 제거한다.
    """
    bullets: list[str] = []
    current: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _BULLET_RE.match(line):
            if current:
                bullets.append(" ".join(current).strip())
            current = [line]
            continue
        if current:
            current.append(line)
        else:
            bullets.append(line)
    if current:
        bullets.append(" ".join(current).strip())
    return bullets


def _normalize(text: str) -> str:
    """유사도 계산용으로 날짜·bullet·구두점을 걷어낸다."""
    text = _BULLET_RE.sub("", text or "")
    text = _DATE_RE.sub(" ", text)
    text = _NON_WORD_RE.sub(" ", text.lower())
    return " ".join(text.split())


def _is_policy_rule(bullet: str) -> bool:
    """bullet이 앞으로의 행동 규칙/운영 정책인지 판단한다."""
    normalized = _normalize(bullet)
    return any(hint.lower() in normalized for hint in _POLICY_HINTS)


def _is_event_log(bullet: str) -> bool:
    """bullet이 완료 로그나 사건 기록인지 판단한다."""
    normalized = _normalize(bullet)
    has_object = any(hint.lower() in normalized for hint in _EVENT_OBJECT_HINTS)
    has_action = any(hint.lower() in normalized for hint in _EVENT_ACTION_HINTS)
    has_story = any(hint.lower() in normalized for hint in _MEMORY_STORY_HINTS)
    return (has_object and has_action) or (has_story and has_action)


def _memory_fingerprints(*texts: str) -> list[str]:
    """MEMORY 계열 본문에서 비교 가능한 bullet fingerprint를 추출한다."""
    fingerprints: list[str] = []
    for text in texts:
        for bullet in _split_bullets(text):
            normalized = _normalize(bullet)
            if normalized:
                fingerprints.append(normalized)
    return fingerprints


def _duplicates_memory(bullet: str, fingerprints: list[str]) -> bool:
    """bullet이 MEMORY 요약/기존 MEMORY.md와 사실상 같은 내용인지 검사한다."""
    normalized = _normalize(bullet)
    if not normalized:
        return False
    for candidate in fingerprints:
        if normalized == candidate:
            return True
        if normalized in candidate or candidate in normalized:
            shorter = min(len(normalized), len(candidate))
            if shorter >= 12:
                return True
        if SequenceMatcher(None, normalized, candidate).ratio() >= 0.82:
            return True
    return False


def filter_agent_updates(
    agent_updates: str,
    *,
    memory_summary: str = "",
    existing_memory_md: str = "",
) -> AgentUpdateFilterResult:
    """AGENT.md에 저장 가능한 dreaming update만 남긴다.

    Args:
        agent_updates: LLM이 반환한 AGENT.md update 본문.
        memory_summary: 같은 dreaming 사이클의 MEMORY.md 요약. 의미가 겹치면 제거한다.
        existing_memory_md: 기존 MEMORY.md 본문. 과거 사건 기록과 중복되면 제거한다.

    Returns:
        보존/제거 목록과 보존 bullet을 합친 최종 본문.
    """
    memory_fingerprints = _memory_fingerprints(memory_summary, existing_memory_md)
    kept: list[str] = []
    dropped: list[str] = []

    for bullet in _split_bullets(agent_updates):
        if _duplicates_memory(bullet, memory_fingerprints):
            dropped.append(bullet)
            continue
        if _is_policy_rule(bullet):
            kept.append(bullet)
            continue
        if _is_event_log(bullet):
            dropped.append(bullet)
            continue
        # 불확실한 항목은 fail-closed: AGENT.md 오염보다 누락이 안전하다. 지속 정책은
        # 프롬프트가 명시적으로 정책 표현을 쓰도록 강제한다.
        dropped.append(bullet)

    return AgentUpdateFilterResult(text="\n".join(kept), kept=kept, dropped=dropped)
