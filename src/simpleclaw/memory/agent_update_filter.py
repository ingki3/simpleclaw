"""AGENT.md dreaming update 필터.

AGENT.md는 에이전트의 지속 행동 규칙과 운영 정책을 담는 파일이다. Dreaming이
레시피/크론 변경 완료 기록 같은 사건 로그를 AGENT.md에 append하면 MEMORY.md와
역할이 겹치고, 이후 시스템 프롬프트 오염으로 이어진다. 이 모듈은 LLM이 뽑은
``agent_updates``를 저장 직전에 bullet 단위로 검증해 정책성 항목만 남긴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_BULLET_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")
_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")
_DATE_RE = re.compile(
    r"(?:\d{4}[-./년]\s*\d{1,2}|\d{1,2}[/월]\s*\d{1,2}|\d{1,2}\s*월\s*\d{1,2}\s*일)"
)
_POLICY_PATTERNS = (
    "앞으로",
    "항상",
    "기본적으로",
    "절대",
    "반드시",
    "하지 말",
    "하지 않는다",
    "할 때는",
    "할 때",
    "규칙",
    "정책",
    "원칙",
    "must",
    "always",
    "never",
)
_EVENT_PATTERNS = (
    "생성함",
    "생성했다",
    "생성됨",
    "수정함",
    "수정했다",
    "변경함",
    "변경했다",
    "변경됨",
    "등록함",
    "등록했다",
    "삭제함",
    "삭제했다",
    "완료함",
    "완료했다",
    "반영함",
    "반영했다",
    "요청 수신",
    "크론 작업의 주기",
    "cron 작업의 주기",
    "레시피를 생성",
    "레시피 생성",
    "작업을 등록",
)
_EVENT_SUBJECTS = (
    "크론",
    "cron",
    "레시피",
    "recipe",
)


@dataclass(frozen=True)
class AgentUpdateFilterResult:
    """AGENT update 필터링 결과와 진단 통계."""

    text: str
    kept: int
    dropped_event: int
    dropped_duplicate: int
    dropped_non_policy: int

    @property
    def dropped(self) -> int:
        """전체 drop 수를 반환한다."""
        return self.dropped_event + self.dropped_duplicate + self.dropped_non_policy


def _split_bullets(text: str) -> list[str]:
    """Markdown bullet 텍스트를 항목 단위로 분리한다."""
    bullets: list[str] = []
    current: list[str] = []
    for line in (text or "").splitlines():
        if _BULLET_RE.match(line):
            if current:
                bullets.append("\n".join(current).strip())
            current = [line.rstrip()]
        elif current and line.strip():
            current.append(line.rstrip())
        elif current:
            bullets.append("\n".join(current).strip())
            current = []
    if current:
        bullets.append("\n".join(current).strip())
    stripped = (text or "").strip()
    return bullets if bullets else ([stripped] if stripped else [])


def _normalize(text: str) -> str:
    """중복 비교용으로 문자열을 보수적으로 정규화한다."""
    return " ".join(_TOKEN_RE.findall(text.lower().replace("`", "")))


def _tokens(text: str) -> set[str]:
    """짧은 조사/불용 수준 토큰을 제외한 비교 토큰 집합을 반환한다."""
    return {tok for tok in _TOKEN_RE.findall(text.lower().replace("`", "")) if len(tok) >= 2}


def _is_policy_rule(bullet: str) -> bool:
    """bullet이 앞으로 적용할 지속 행동 규칙처럼 보이는지 판정한다."""
    lowered = bullet.lower()
    return any(pattern in lowered for pattern in _POLICY_PATTERNS)


def _is_event_log(bullet: str) -> bool:
    """bullet이 과거 사건/작업 완료 로그처럼 보이는지 판정한다."""
    lowered = bullet.lower()
    if _DATE_RE.search(bullet):
        return True
    if any(pattern in lowered for pattern in _EVENT_PATTERNS):
        return True
    return any(subject in lowered for subject in _EVENT_SUBJECTS) and any(
        verb in lowered
        for verb in (
            "생성",
            "수정",
            "변경",
            "등록",
            "삭제",
            "완료",
            "반영",
        )
    )


def _is_duplicate_of_memory(bullet: str, memory_text: str | None) -> bool:
    """MEMORY 요약/기존 MEMORY.md와 의미가 크게 겹치면 True를 반환한다."""
    if not memory_text:
        return False
    bullet_norm = _normalize(bullet)
    if not bullet_norm:
        return False
    memory_norm = _normalize(memory_text)
    if bullet_norm and bullet_norm in memory_norm:
        return True
    bullet_tokens = _tokens(bullet)
    if len(bullet_tokens) < 3:
        return False
    for memory_bullet in _split_bullets(memory_text):
        memory_tokens = _tokens(memory_bullet)
        if len(memory_tokens) < 3:
            continue
        overlap = len(bullet_tokens & memory_tokens) / len(bullet_tokens | memory_tokens)
        if overlap >= 0.55:
            return True
    return False


def filter_agent_updates_with_stats(
    text: str,
    *,
    memory_text: str | None = None,
) -> AgentUpdateFilterResult:
    """AGENT.md에 저장 가능한 지속 정책 bullet만 남긴다.

    Args:
        text: LLM이 생성한 ``agent_updates`` 원문.
        memory_text: 같은 dreaming 결과의 memory summary 또는 기존 MEMORY.md 텍스트.

    Returns:
        필터링된 텍스트와 kept/dropped 통계.
    """
    kept: list[str] = []
    dropped_event = 0
    dropped_duplicate = 0
    dropped_non_policy = 0
    for bullet in _split_bullets(text):
        if not _is_policy_rule(bullet):
            dropped_non_policy += 1
            continue
        if _is_event_log(bullet):
            dropped_event += 1
            continue
        if _is_duplicate_of_memory(bullet, memory_text):
            dropped_duplicate += 1
            continue
        kept.append(bullet)
    return AgentUpdateFilterResult(
        text="\n".join(kept),
        kept=len(kept),
        dropped_event=dropped_event,
        dropped_duplicate=dropped_duplicate,
        dropped_non_policy=dropped_non_policy,
    )


def filter_agent_updates(text: str, *, memory_text: str | None = None) -> str:
    """AGENT.md 저장 전 ``agent_updates``를 정제한 텍스트만 반환한다."""
    return filter_agent_updates_with_stats(text, memory_text=memory_text).text
