"""질문 시 Agent Study Wiki 에서 관련 배경지식을 회수해 프롬프트 블록으로 조립한다.

Study Wiki 는 사용자 메모리/프로필이 아니라, 에이전트가 사용자의 질문에 답하기
위해 미리 공부해 둔 *외부 세계 배경지식*이다(전체 설계는
`.hermes/plans/2026-06-29_095400-agent-study-wiki.md` 참고). 이 모듈은 그 wiki 의
on-disk 레이아웃(`topics.yaml` + `topics/<id>.md`)을 읽어, 현재 질문과 관련된
topic/page 를 골라 system prompt 에 주입할 "## Agent Study Context" 블록을 만든다.

설계 결정 — 실패 격리:
    study 회수는 기존 대화 RAG/장기기억 회수와 *독립적으로* 실패해야 한다. wiki 가
    없거나(기능 off), 파일이 깨졌거나, 한 topic page 파싱이 실패해도 빈 문자열 또는
    부분 결과를 돌려주고 절대 예외를 위로 던지지 않는다. 대화 응답 흐름이 study
    저장소 상태에 인질로 잡히면 안 되기 때문이다.

설계 결정 — 결정적(deterministic) 동작:
    검색은 lexical token overlap 으로 점수화한다(임베딩 인덱스는 후속 이슈 범위).
    동점은 안정 정렬(점수 → 최근 갱신 → topic id)로 깨고, context budget 초과 시
    랭킹 순서대로 블록을 채우다 다음 블록이 예산을 넘기면 멈춘다. 같은 입력은 항상
    같은 출력을 낸다.

설계 결정 — 사용자 메모리와의 분리 명시:
    블록 헤더에 "이는 외부 배경지식이며 사용자 메모리가 아니다 / 최신·현재 사실은
    다시 확인하라"는 경고를 넣는다. LLM 이 study context 를 사용자 프로필 사실처럼
    단정하거나, 낡은 정보를 현재 사실로 답하는 사고를 막기 위함이다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .markdown import parse_study_page
from .paths import topic_page_path
from .topic_registry import load_topics
from .types import StudyPage, StudySource, StudyTopic

logger = logging.getLogger(__name__)

# 블록 최상단 안내문. test_context_retrieval_agent_study 가 헤더 존재를 검증한다.
STUDY_CONTEXT_HEADER = "## Agent Study Context"

# 사용자 메모리/프로필과의 분리 + 현재성 재확인 경고.
# 주의: "사용자 프로필" 이라는 연속 문자열은 넣지 않는다 — 회귀 테스트가 이 context
# 를 사용자 프로필 블록과 혼동하지 않는지 확인하기 위해 그 부재를 검사한다.
_PURPOSE_LINE_EN = (
    "Purpose: The following is background knowledge the agent studied for this "
    "user. It is not a user profile fact. Verify live/current facts when needed."
)
_PURPOSE_LINE_KO = (
    "참고: 아래는 에이전트가 미리 공부한 외부 배경지식이며, 사용자 메모리(선호·"
    "프로필 사실)가 아닙니다. 최신·현재 사실은 답변 전에 다시 확인하세요."
)

# archived topic 은 일반 질문에서는 검색 후보에서 제외하고, 사용자가 과거/역사적
# 배경을 물을 때만 fallback 으로 허용한다.
_ARCHIVED_STATUSES = {"archived"}
# 활성/고정 topic 은 동일 점수에서 archived 보다 우선한다.
_PINNED_STATUSES = {"pinned"}

# 한 topic 블록에 넣을 관련 노트/출처 상한 — 프롬프트 비대화 방지.
_MAX_NOTES_PER_TOPIC = 4
_MAX_SOURCES_PER_TOPIC = 3


@dataclass(frozen=True)
class StudyRetrievalConfig:
    """Study 회수에 필요한 설정값 묶음.

    Attributes:
        enabled: 회수 활성 여부. ``False`` 면 항상 빈 문자열을 돌려준다.
        wiki_dir: wiki 루트 디렉터리(``topics.yaml`` / ``topics/`` 의 부모).
        top_k: context 에 넣을 최대 topic 수.
        max_context_chars: 블록 전체 문자 예산(초과 시 결정적으로 절단).
    """

    enabled: bool
    wiki_dir: Path
    top_k: int = 4
    max_context_chars: int = 5000


@dataclass(frozen=True)
class StudyTopicMatch:
    """질문과 매칭된 topic 한 건과 그 회수 메타데이터."""

    topic: StudyTopic
    page: StudyPage | None
    score: float
    notes: tuple[str, ...] = ()
    sources: tuple[StudySource, ...] = ()


# 한국어/영문 단어 토큰화 — context_retrieval 의 lexical 보강과 동일한 규약.
_TOKEN_RE = re.compile(r"[\w가-힣]+")


def _tokens(text: str) -> set[str]:
    """질의/문서를 lexical overlap 점수용 토큰 집합으로 나눈다(2자 이상)."""
    return {t.lower() for t in _TOKEN_RE.findall(text or "") if len(t) >= 2}


def _is_archived(topic: StudyTopic) -> bool:
    """topic 이 archived 상태인지 — 일반 질문 검색에서 제외 대상인지 판별."""
    return (topic.status or "").strip().lower() in _ARCHIVED_STATUSES


def _status_rank(topic: StudyTopic) -> int:
    """정렬 보조용 상태 우선순위(높을수록 우선): pinned > 일반 active > archived."""
    status = (topic.status or "").strip().lower()
    if status in _PINNED_STATUSES:
        return 2
    if status in _ARCHIVED_STATUSES:
        return 0
    return 1


class StudyRetriever:
    """Agent Study Wiki 를 읽어 질문 관련 배경지식 블록을 만든다.

    회수는 순수 디스크 읽기 + lexical 매칭으로만 동작하며, 어떤 단계가 실패해도
    예외를 던지지 않고 가능한 한 부분 결과(또는 빈 문자열)를 돌려준다.
    """

    def __init__(self, config: StudyRetrievalConfig) -> None:
        """회수 설정을 보관한다.

        Args:
            config: :class:`StudyRetrievalConfig`. ``enabled=False`` 면 회수를 건너뛴다.
        """
        self._config = config
        self._wiki_dir = Path(config.wiki_dir).expanduser()

    @property
    def enabled(self) -> bool:
        """회수 기능 활성 여부."""
        return bool(self._config.enabled)

    def retrieve_context(self, user_text: str, *, historical: bool = False) -> str:
        """질문과 관련된 Study Wiki context 블록을 만든다(실패 시 빈 문자열).

        Args:
            user_text: 사용자 질문 원문.
            historical: 사용자가 과거/역사적 배경을 명시적으로 물었는지. ``True`` 면
                archived topic 도 검색 후보에 포함한다.

        Returns:
            "## Agent Study Context" 로 시작하는 프롬프트 블록. 관련 topic 이 없거나
            기능이 꺼져 있으면 빈 문자열.
        """
        if not self.enabled:
            return ""
        if not (user_text and user_text.strip()):
            return ""
        try:
            matches = self._match_topics(user_text, historical=historical)
        except Exception as exc:  # noqa: BLE001 — study 회수 장애가 대화 응답을 막아선 안 됨
            logger.warning("Study retrieval failed: %s", exc)
            return ""
        if not matches:
            return ""
        return self._format_block(matches)

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _match_topics(self, user_text: str, *, historical: bool) -> list[StudyTopicMatch]:
        """topics.yaml + 각 page 를 읽어 질문과 관련된 topic 을 점수순으로 추린다."""
        query_tokens = _tokens(user_text)
        if not query_tokens:
            return []

        topics_path = self._wiki_dir / "topics.yaml"
        topics = load_topics(topics_path)
        if not topics:
            return []

        matches: list[StudyTopicMatch] = []
        for topic in topics:
            # archived 는 사용자가 과거 배경을 물을 때(historical)만 후보로 둔다.
            if _is_archived(topic) and not historical:
                continue
            page = self._load_page(topic)
            score, notes = self._score_topic(topic, page, query_tokens)
            if score <= 0.0:
                continue
            sources = tuple(page.sources[:_MAX_SOURCES_PER_TOPIC]) if page else ()
            matches.append(
                StudyTopicMatch(
                    topic=topic,
                    page=page,
                    score=score,
                    notes=tuple(notes),
                    sources=sources,
                )
            )

        # 점수 내림차순 → 상태 우선순위(pinned>active>archived) → 최근 갱신 → id 로
        # 안정 정렬해 동점에서도 결정적 순서를 보장한다.
        matches.sort(
            key=lambda m: (
                m.score,
                _status_rank(m.topic),
                m.topic.updated_at or "",
                m.topic.id,
            ),
            reverse=True,
        )
        return matches[: max(1, self._config.top_k)]

    def _load_page(self, topic: StudyTopic) -> StudyPage | None:
        """topic 의 Markdown page 를 읽어 ``StudyPage`` 로 파싱한다(없으면 ``None``)."""
        try:
            page_path = topic_page_path(topic.id, base=self._wiki_dir)
        except ValueError:
            # topic_id 에 경로 구분자가 있는 등 파일명으로 못 쓰는 경우 — page 없이 진행.
            return None
        if not page_path.is_file():
            return None
        try:
            text = page_path.read_text(encoding="utf-8")
            return parse_study_page(text, page_path)
        except (OSError, ValueError) as exc:
            logger.warning("Study page parse failed for %s: %s", topic.id, exc)
            return None

    def _score_topic(
        self,
        topic: StudyTopic,
        page: StudyPage | None,
        query_tokens: set[str],
    ) -> tuple[float, list[str]]:
        """topic+page 의 질문 관련도를 점수화하고 관련 노트를 함께 고른다.

        점수는 (1) topic 메타(label/description/tags)와 (2) page 본문 불릿의 질문
        토큰 overlap 으로 산출한다. label 매칭은 page 본문 매칭보다 가중치를 더 둔다
        (주제 자체가 질문과 일치하는 신호가 더 강하기 때문).

        Returns:
            ``(score, notes)`` — score 가 0 이면 관련 없음. notes 는 질문과 겹치는
            page 불릿(없으면 답변 주의사항/현재 상태 fallback).
        """
        label_tokens = _tokens(f"{topic.label} {topic.description} {' '.join(topic.tags)}")
        label_overlap = len(label_tokens & query_tokens)
        score = 1.5 * label_overlap

        notes: list[str] = []
        if page is not None:
            # 질문 토큰과 겹치는 본문 불릿을 관련 노트로 모은다. "답변 시 주의사항"은
            # 항상 우선해 신뢰/현재성 경고가 누락되지 않게 한다.
            scored_bullets: list[tuple[int, int, str]] = []
            section_order = [
                page.answer_guidance,
                page.current_state,
                page.personal_relevance,
                page.historical_context,
            ]
            for section_rank, bullets in enumerate(section_order):
                for bullet in bullets:
                    overlap = len(_tokens(bullet) & query_tokens)
                    if overlap > 0:
                        scored_bullets.append((overlap, -section_rank, bullet))
            scored_bullets.sort(key=lambda x: (x[0], x[1]), reverse=True)
            body_overlap = sum(b[0] for b in scored_bullets)
            score += float(body_overlap)
            notes = [b[2] for b in scored_bullets[:_MAX_NOTES_PER_TOPIC]]

            # 본문에서 직접 겹치는 노트가 없지만 label 로 매칭됐다면, 답변 주의사항/
            # 현재 상태를 fallback 으로 보여 빈 블록을 피한다.
            if not notes and label_overlap > 0:
                fallback = (page.answer_guidance or page.current_state)[:_MAX_NOTES_PER_TOPIC]
                notes = list(fallback)

        return score, notes

    def _topic_confidence(self, match: StudyTopicMatch) -> float | None:
        """블록에 표기할 topic 신뢰도 — page 출처 confidence 중 최댓값을 쓴다.

        page/출처에 confidence 정보가 전혀 없으면 ``None`` 을 돌려 줄을 생략한다
        (근거 없는 숫자를 만들어내지 않는다).
        """
        confidences = [s.confidence for s in match.sources if s.confidence]
        if confidences:
            return max(confidences)
        return None

    def _render_topic(self, match: StudyTopicMatch) -> str:
        """topic 한 건을 블록 항목(불릿) Markdown 으로 직렬화한다."""
        topic = match.topic
        lines = [f"- Topic: {topic.label}"]
        updated = (match.page.updated_at if match.page else None) or topic.updated_at
        if updated:
            lines.append(f"  Updated: {updated}")
        confidence = self._topic_confidence(match)
        if confidence is not None:
            lines.append(f"  Confidence: {confidence:.2f}")
        if match.notes:
            lines.append("  Relevant notes:")
            lines.extend(f"  - {note}" for note in match.notes)
        if match.sources:
            lines.append("  Sources:")
            for source in match.sources:
                lines.append(f"  - {source.url or source.title}")
        return "\n".join(lines)

    def _format_block(self, matches: list[StudyTopicMatch]) -> str:
        """topic 매치 목록을 헤더 + 항목 블록으로 조립하고 예산 내로 절단한다.

        헤더(안내문)는 항상 유지하고, topic 항목은 랭킹 순서대로 채우다 다음 항목이
        ``max_context_chars`` 를 넘기면 멈춘다(결정적 절단). 한 항목조차 예산을
        넘으면 그 항목 텍스트를 문자 경계에서 잘라 최소 한 건은 싣는다.
        """
        header = "\n".join([STUDY_CONTEXT_HEADER, _PURPOSE_LINE_EN, _PURPOSE_LINE_KO])
        budget = max(0, int(self._config.max_context_chars))

        rendered = [self._render_topic(m) for m in matches]
        kept: list[str] = []
        # 헤더 + 항목 사이 구분("\n\n") 길이를 누적해 예산을 정확히 지킨다.
        total = len(header)
        for item in rendered:
            addition = len(item) + 2  # 앞에 붙는 "\n\n"
            if total + addition <= budget:
                kept.append(item)
                total += addition
            elif not kept:
                # 첫 항목조차 예산을 넘기면 문자 경계에서 잘라 최소 한 건은 싣는다.
                remaining = budget - total - 2
                if remaining > 1:
                    kept.append(item[: remaining - 1].rstrip() + "…")
                break
            else:
                break

        if not kept:
            # 예산이 헤더조차 못 담을 만큼 작으면 헤더만 예산 길이로 절단해 돌려준다.
            return header[:budget] if budget else header

        return header + "\n\n" + "\n\n".join(kept)
