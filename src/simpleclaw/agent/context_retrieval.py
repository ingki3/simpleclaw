"""Agent context retrieval service — RAG와 장기기억 회수 조립 전담.

오케스트레이터가 LLM/tool loop 제어에 집중하도록, 과거 대화 RAG와 Dreaming
장기기억을 조회하고 시스템 프롬프트용 context 블록으로 포맷하는 책임을 이 모듈로
분리한다. 각 source(conversation, insight, active project, memory item)는 독립적으로
실패해도 나머지 회상 경로와 일반 응답 흐름을 유지한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from simpleclaw.memory.active_projects import ActiveProjectStore, filter_active
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.embedding_service import EmbeddingService
from simpleclaw.memory.insights import InsightStore, is_promoted
from simpleclaw.memory.models import MemoryItemType
from simpleclaw.memory.supersession import is_expired_event_memory

if TYPE_CHECKING:
    from simpleclaw.logging.structured_logger import StructuredLogger
    from simpleclaw.study.retriever import StudyRetriever

logger = logging.getLogger(__name__)

# BIZ-394 — Study context freshness/confidence gate 기본값.
# study context 는 배경지식이므로, 마지막 갱신이 오래됐거나 출처 confidence 가 낮으면
# 현재 사실로 단정해선 안 된다. 이 임계값을 넘기면 블록에 신선도 경고와 한계 명시
# 강제 지시문을 심어, 답변이 반드시 한계를 밝히거나 실시간 재조회를 하도록 한다.
STUDY_STALE_AFTER_DAYS = 14
STUDY_MIN_CONFIDENCE = 0.5
# 라우터(response_router)가 stale 여부를 파싱하는 마커. 자유 텍스트와 무관히 한 줄.
_STUDY_FRESHNESS_STALE_MARKER = "Freshness: stale"
# 블록에서 "Updated: <iso>" / "Confidence: <float>" 메타를 뽑는 정규식.
_STUDY_UPDATED_RE = re.compile(r"updated\s*[:=]\s*([0-9T:\-+.Zz ]{8,40})", re.IGNORECASE)
_STUDY_CONFIDENCE_RE = re.compile(r"confidence\s*[:=]\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)


def _parse_iso_datetime(raw: str) -> datetime | None:
    """블록의 Updated 값을 timezone-aware datetime 으로 보수적으로 파싱한다.

    날짜만 있거나(``2026-06-01``) ``Z`` 표기여도 깨지지 않게 흡수하고, 파싱 불가면
    ``None`` 을 돌려 신선도 판정에서 제외한다(근거 없는 stale 판정 방지).
    """
    value = (raw or "").strip().rstrip(".")
    if not value:
        return None
    candidate = value.replace("Z", "+00:00").replace("z", "+00:00")
    for text in (candidate, candidate[:10]):
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    return None


def annotate_study_freshness(
    study_block: str,
    *,
    now: datetime,
    stale_after_days: int = STUDY_STALE_AFTER_DAYS,
    min_confidence: float = STUDY_MIN_CONFIDENCE,
) -> str:
    """stale/저신뢰 study context 에 신선도 경고 + 한계 명시 강제 지시문을 덧붙인다.

    study context 의 ``Updated:`` 최신 시각이 ``stale_after_days`` 보다 오래됐거나,
    출처 ``Confidence:`` 최솟값이 ``min_confidence`` 미만이면 블록 끝에 경고 섹션을
    더한다. 경고 섹션은 (1) 라우터가 파싱할 ``Freshness: stale`` 마커와 (2) LLM 이
    답변에서 반드시 한계를 밝히거나 실시간 재조회하도록 강제하는 지시문을 담는다.

    Args:
        study_block: ``StudyRetriever`` 가 만든 study context 블록(비어 있으면 그대로).
        now: 신선도 비교 기준 시각(timezone-aware 권장).
        stale_after_days: 이 일수보다 오래된 갱신이면 stale.
        min_confidence: 이 값 미만 confidence 면 저신뢰.

    Returns:
        필요 시 경고 섹션이 덧붙은 블록(아니면 입력 그대로).
    """
    block = (study_block or "").rstrip()
    if not block:
        return study_block

    now_aware = now if now.tzinfo is not None else now.replace(tzinfo=UTC)

    updated_dts = [
        dt for dt in (_parse_iso_datetime(m) for m in _STUDY_UPDATED_RE.findall(block)) if dt
    ]
    newest = max(updated_dts) if updated_dts else None
    age_days = (now_aware - newest).total_seconds() / 86400.0 if newest else None
    is_stale = age_days is not None and age_days > max(0, int(stale_after_days))

    confidences = [float(m) for m in _STUDY_CONFIDENCE_RE.findall(block)]
    min_conf = min(confidences) if confidences else None
    is_low_conf = min_conf is not None and min_conf < float(min_confidence)

    if not (is_stale or is_low_conf):
        return study_block

    reasons: list[str] = []
    if is_stale and age_days is not None:
        reasons.append(f"마지막 갱신이 약 {int(age_days)}일 전입니다")
    if is_low_conf and min_conf is not None:
        reasons.append(f"출처 confidence 가 낮습니다(min={min_conf:.2f})")
    reason_text = "; ".join(reasons) or "신선도/신뢰도가 충분하지 않습니다"

    warning = "\n".join(
        [
            "",
            "## Agent Study Context — Freshness Warning",
            # 라우터가 파싱하는 결정적 마커. stale 또는 저신뢰면 항상 stale 로 표기해
            # 현재 사실 재조회(at-least guarded)를 강제하는 신호로 쓴다.
            _STUDY_FRESHNESS_STALE_MARKER,
            f"경고: 위 배경지식은 신뢰 근거로 쓰기에 한계가 있습니다 — {reason_text}.",
            ("배경지식만으로 현재 사실을 단정하지 마세요. 답변에 시점·불확실성 한계를 "
            "명시하거나, 실시간 조회로 재확인한 뒤 답하세요."),
        ]
    )
    return f"{block}\n{warning}"


@dataclass(frozen=True)
class ContextRetrievalConfig:
    """context retrieval에 필요한 설정값 묶음."""

    rag_top_k: int
    rag_threshold: float
    long_term_enabled: bool
    long_term_top_k: int
    long_term_min_confidence: float
    long_term_promotion_threshold: int
    long_term_context_budget_chars: int
    long_term_per_item_chars: int
    long_term_insights_file: str | Path
    long_term_active_projects_file: str | Path
    long_term_active_projects_window_days: int


class ContextRetrievalService:
    """과거 대화 RAG와 Dreaming 장기기억을 시스템 프롬프트 context로 회수한다."""

    def __init__(
        self,
        *,
        store: ConversationStore | Any,
        embedding_service: EmbeddingService | None,
        config: ContextRetrievalConfig,
        structured_logger: StructuredLogger | None = None,
        study_retriever: StudyRetriever | None = None,
        now_provider: Callable[[], datetime] | None = None,
        study_stale_after_days: int = STUDY_STALE_AFTER_DAYS,
        study_min_confidence: float = STUDY_MIN_CONFIDENCE,
    ) -> None:
        """오케스트레이터에서 생성된 store/service와 retrieval 설정을 보관한다.

        ``study_retriever`` 는 Agent Study Wiki 회수기(BIZ-393)다. ``None`` 이거나
        비활성이면 study context 를 붙이지 않으며, 회수가 실패해도 대화 RAG/장기기억
        회수와 독립적으로 격리된다.

        ``now_provider`` 는 study context 신선도 비교용 시계다(테스트 주입용). 기본은
        UTC now. ``study_stale_after_days`` / ``study_min_confidence`` 는 BIZ-394
        freshness/confidence gate 임계값으로, 초과 시 study 블록에 한계 명시 강제
        지시문을 심는다.
        """
        self._store = store
        self._embedding_service = embedding_service
        self._structured_logger = structured_logger
        self._study_retriever = study_retriever
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._study_stale_after_days = study_stale_after_days
        self._study_min_confidence = study_min_confidence
        self._rag_top_k = config.rag_top_k
        self._rag_threshold = config.rag_threshold
        self._long_term_enabled = config.long_term_enabled
        self._long_term_top_k = config.long_term_top_k
        self._long_term_min_confidence = config.long_term_min_confidence
        self._long_term_promotion_threshold = config.long_term_promotion_threshold
        self._long_term_context_budget_chars = config.long_term_context_budget_chars
        self._long_term_per_item_chars = config.long_term_per_item_chars
        self._long_term_insights_file = Path(config.long_term_insights_file).expanduser()
        self._long_term_active_projects_file = Path(
            config.long_term_active_projects_file
        ).expanduser()
        self._long_term_active_projects_window_days = (
            config.long_term_active_projects_window_days
        )

    async def retrieve(
        self,
        user_text: str,
        exclude_contents: set[str] | None = None,
    ) -> str:
        """대화 RAG·장기기억 회수에 Agent Study Wiki 배경지식을 더해 포맷한다.

        두 회수 경로는 서로 독립이다. study 회수는 임베딩(RAG) 활성 여부와 무관하게
        동작하며(자체 lexical 매칭), 어느 한쪽이 실패해도 다른 쪽 결과는 유지된다.
        """
        rag_context = await self._retrieve_conversation_context(user_text, exclude_contents)
        study_context = self._retrieve_study_context(user_text)
        return "\n\n".join(part for part in (rag_context, study_context) if part)

    def _retrieve_study_context(self, user_text: str) -> str:
        """Agent Study Wiki 배경지식 블록을 회수한다(없거나 실패하면 빈 문자열).

        RAG/장기기억 회수와 완전히 분리된 실패 격리 지점이다. retriever 자체도
        내부에서 예외를 삼키지만, 여기서도 한 번 더 감싸 study 저장소 장애가 대화
        응답 흐름으로 새지 않도록 이중으로 보호한다. BIZ-394: 회수된 블록이 stale/
        저신뢰면 신선도 경고 + 한계 명시 강제 지시문을 덧붙인다.
        """
        if self._study_retriever is None or not self._study_retriever.enabled:
            return ""
        try:
            block = self._study_retriever.retrieve_context(user_text)
        except Exception as exc:
            logger.warning("Study context retrieval failed: %s", exc)
            return ""
        return self._annotate_study_freshness(block)

    def _annotate_study_freshness(self, block: str) -> str:
        """study 블록에 stale/저신뢰 경고를 덧붙인다(실패해도 원본 블록 유지)."""
        if not block:
            return block
        try:
            return annotate_study_freshness(
                block,
                now=self._now_provider(),
                stale_after_days=self._study_stale_after_days,
                min_confidence=self._study_min_confidence,
            )
        except Exception as exc:
            logger.warning("Study freshness annotation failed: %s", exc)
            return block

    def study_context_for_routing(self, user_text: str) -> str:
        """라우팅 단계에서 freshness gate 신호로 쓸 study 블록을 회수·주석한다.

        오케스트레이터가 route 결정 전에 호출한다. 회수/주석 모두 내부에서 실패를
        삼키므로, 장애 시 빈 문자열을 돌려 라우팅을 막지 않는다.
        """
        return self._retrieve_study_context(user_text)

    async def _retrieve_conversation_context(
        self,
        user_text: str,
        exclude_contents: set[str] | None = None,
    ) -> str:
        """과거 대화 RAG와 Dreaming 장기기억을 함께 회수해 프롬프트 블록으로 포맷한다."""
        start = time.perf_counter()
        excluded = exclude_contents or set()
        source_stats: dict[str, dict[str, object]] = {
            "conversation": {"count": 0, "hit": False, "top_score": None, "errors": 0},
            "long_term": {"count": 0, "hit": False, "top_score": None, "errors": 0},
            "cluster_summary": {"count": 0, "hit": False, "top_score": None, "errors": 0},
        }

        def _error_count(stats: dict[str, object]) -> int:
            """구조화 로그 상태 계산용 error counter를 정수로 반환한다."""
            raw = stats.get("errors")
            return raw if isinstance(raw, int) else 0

        def _increment_source_error(source: str) -> None:
            """source_stats의 error counter를 타입 안전하게 증가시킨다."""
            source_stats[source]["errors"] = _error_count(source_stats[source]) + 1

        def _log(
            *,
            status: str,
            hit: bool,
            candidates: int = 0,
            recalled_messages: int = 0,
            recalled_tokens: int = 0,
            top_score: float | None = None,
            error: str | None = None,
            context_chars: int = 0,
        ) -> None:
            """retrieval 관찰성 이벤트를 구조화 로그로 남기되 실패는 삼킨다."""
            if self._structured_logger is None:
                return
            details: dict = {
                "hit": hit,
                "candidates": candidates,
                "recalled_messages": recalled_messages,
                "recalled_tokens": recalled_tokens,
                "top_k": self._rag_top_k,
                "threshold": self._rag_threshold,
                "context_chars": context_chars,
                **source_stats,
            }
            if top_score is not None:
                details["top_score"] = round(float(top_score), 4)
            if error is not None:
                details["error"] = error
            try:
                self._structured_logger.log(
                    action_type="rag_retrieve",
                    input_summary=user_text,
                    output_summary=f"recalled={recalled_messages} tokens={recalled_tokens}",
                    duration_ms=(time.perf_counter() - start) * 1000.0,
                    status=status,
                    **details,
                )
            except Exception as exc:
                logger.warning("RAG structured log write failed: %s", exc)

        if self._embedding_service is None or not self._embedding_service.is_enabled:
            _log(status="skipped", hit=False, error="rag_disabled")
            return ""

        try:
            query_vec = await asyncio.to_thread(
                self._embedding_service.encode_query, user_text
            )
        except Exception as exc:
            logger.warning("RAG query encoding failed: %s", exc)
            _log(status="error", hit=False, error=f"encode:{exc}"[:200])
            return ""
        if query_vec is None:
            _log(status="skipped", hit=False, error="encode_returned_none")
            return ""

        def _tokens(text: str) -> set[str]:
            """한국어/영문 단어를 단순 lexical 보강 점수용 토큰으로 나눈다."""
            return {t.lower() for t in re.findall(r"[\w가-힣]+", text) if len(t) >= 2}

        query_tokens = _tokens(user_text)

        def _lexical_score(text: str, base: float = 0.0) -> float:
            """semantic 점수가 없는 sidecar 항목에 query overlap 보강 점수를 부여한다."""
            toks = _tokens(text)
            if not toks or not query_tokens:
                return base
            overlap = len(toks & query_tokens)
            return base + (overlap / max(len(query_tokens), 1))

        def _clip(text: str, limit: int | None = None) -> str:
            """context 예산을 지키도록 장기기억 항목 텍스트를 compact하게 자른다."""
            limit = limit or self._long_term_per_item_chars
            compact = " ".join(text.split())
            if len(compact) <= limit:
                return compact
            return compact[: max(0, limit - 1)].rstrip() + "…"

        conversation_lines: list[str] = []
        recalled_tokens = 0
        top_score: float | None = None
        errors = 0
        try:
            results = await asyncio.to_thread(
                self._store.search_similar,
                query_vec,
                self._rag_top_k,
            )
        except Exception as exc:
            logger.warning("RAG conversation search failed: %s", exc)
            source_stats["conversation"]["errors"] = 1
            errors += 1
            results = []
        conversation_candidates = len(results)
        top_score = results[0][1] if results else None
        source_stats["conversation"]["top_score"] = (
            round(float(top_score), 4) if top_score is not None else None
        )
        for msg, score in results:
            if score < self._rag_threshold:
                continue
            if msg.content in excluded:
                continue
            ts = msg.timestamp.strftime("%Y-%m-%d %H:%M")
            conversation_lines.append(f"- [{ts}] **{msg.role.value}**: {msg.content}")
            recalled_tokens += int(msg.token_count or 0)
        source_stats["conversation"]["count"] = len(conversation_lines)
        source_stats["conversation"]["hit"] = bool(conversation_lines)

        long_term_candidates: list[tuple[float, str, str]] = []
        if self._long_term_enabled:
            try:
                if self._long_term_insights_file.is_file():
                    for line_no, line in enumerate(
                        self._long_term_insights_file.read_text(encoding="utf-8").splitlines(),
                        start=1,
                    ):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            json.loads(line)
                        except json.JSONDecodeError as exc:
                            logger.warning(
                                "Skipping malformed insight line %d in %s: %s",
                                line_no,
                                self._long_term_insights_file,
                                exc,
                            )
                            _increment_source_error("long_term")
                            errors += 1
                insights = InsightStore(self._long_term_insights_file).load()
                for insight in insights.values():
                    if insight.is_inactive():
                        continue
                    if is_expired_event_memory(f"{insight.topic} {insight.text}"):
                        continue
                    if insight.confidence < self._long_term_min_confidence:
                        continue
                    if not is_promoted(insight, self._long_term_promotion_threshold):
                        continue
                    raw = f"{insight.topic} {insight.text}"
                    score = _lexical_score(raw, insight.confidence + insight.evidence_count * 0.01)
                    if score <= insight.confidence and query_tokens:
                        continue
                    line = (
                        f"- [insight] {insight.topic}: {_clip(insight.text)} "
                        f"(confidence={insight.confidence:.2f}, evidence={insight.evidence_count})"
                    )
                    long_term_candidates.append((score, insight.text, line))
            except Exception as exc:
                logger.warning("Long-term insight retrieval failed: %s", exc)
                _increment_source_error("long_term")
                errors += 1

            try:
                projects = ActiveProjectStore(self._long_term_active_projects_file).load()
                active_projects = filter_active(
                    projects,
                    self._long_term_active_projects_window_days,
                )
                for project in active_projects:
                    text = f"{project.name} {project.role} {project.recent_summary}"
                    if is_expired_event_memory(text):
                        continue
                    if text in excluded:
                        continue
                    score = _lexical_score(text, 0.85)
                    if score <= 0.85 and query_tokens:
                        continue
                    line = (
                        f"- [active_project] {project.name}: {_clip(project.recent_summary)}"
                    )
                    if project.role:
                        line += f" (role={project.role})"
                    long_term_candidates.append((score, text, line))
            except Exception as exc:
                logger.warning("Active-project retrieval failed: %s", exc)
                _increment_source_error("long_term")
                errors += 1

            try:
                memory_hits = self._store.search_memory_items(
                    query_vec,
                    k=max(self._long_term_top_k * 2, 5),
                    min_score=self._rag_threshold,
                    min_confidence=self._long_term_min_confidence,
                )
                for item, similarity in memory_hits:
                    if item.type is MemoryItemType.CLUSTER_SUMMARY:
                        continue
                    if item.text in excluded:
                        continue
                    score = similarity + item.confidence + (item.importance * 0.1)
                    try:
                        self._store.mark_memory_item_accessed(item.id)
                    except Exception as exc:
                        logger.warning("Memory item access mark failed: %s", exc)
                    long_term_candidates.append((
                        score,
                        item.text,
                        (f"- [memory_item:{item.type.value}] {_clip(item.text)} "
                        f"(confidence={item.confidence:.2f}, importance={item.importance:.2f})"),
                    ))
            except Exception as exc:
                logger.warning("Memory item retrieval failed: %s", exc)
                _increment_source_error("long_term")
                errors += 1

        seen_texts = {" ".join(t.split()).lower() for t in excluded}
        long_term_lines: list[str] = []
        long_term_candidates.sort(key=lambda x: x[0], reverse=True)
        ranked_long_term_candidates = long_term_candidates[: self._long_term_top_k]
        for _score, text, line in ranked_long_term_candidates:
            norm = " ".join(text.split()).lower()
            if norm in seen_texts:
                continue
            seen_texts.add(norm)
            long_term_lines.append(line)
            if len(long_term_lines) >= self._long_term_top_k:
                break
        source_stats["long_term"]["count"] = len(long_term_lines)
        source_stats["long_term"]["hit"] = bool(long_term_lines)
        if long_term_candidates:
            source_stats["long_term"]["top_score"] = round(float(long_term_candidates[0][0]), 4)

        cluster_lines: list[str] = []

        sections: list[str] = []
        if long_term_lines:
            sections.append(
                "## 장기기억\n\n"
                "Dreaming/InsightStore가 승격한 durable 사용자·프로젝트 맥락입니다.\n\n"
                + "\n".join(long_term_lines)
            )
        if conversation_lines:
            sections.append(
                "## 관련 과거 대화 (시맨틱 회상)\n\n"
                "아래는 현재 질문과 의미상 유사한 과거 대화입니다. "
                "최근 메시지 윈도우 밖의 정보일 수 있으니 응답 근거로 활용하세요.\n\n"
                + "\n".join(conversation_lines)
            )
        if cluster_lines:
            sections.append(
                "## 클러스터 요약\n\n"
                "Dreaming이 누적 대화를 주제별로 압축한 요약입니다.\n\n"
                + "\n".join(cluster_lines)
            )

        context = "\n\n".join(sections)
        if len(context) > self._long_term_context_budget_chars:
            kept: list[str] = []
            total = 0
            for section in sections:
                if total + len(section) + (2 if kept else 0) <= self._long_term_context_budget_chars:
                    kept.append(section)
                    total += len(section) + (2 if kept else 0)
            context = "\n\n".join(kept)[: self._long_term_context_budget_chars]

        any_hit = bool(context)
        status = "partial" if errors and any_hit else "error" if errors else "success"
        if not any_hit and not errors:
            status = "success"
        best_scores = [
            float(s["top_score"])
            for s in source_stats.values()
            if s.get("top_score") is not None
        ]
        _log(
            status=status,
            hit=any_hit,
            candidates=conversation_candidates + len(long_term_candidates),
            recalled_messages=len(conversation_lines),
            recalled_tokens=recalled_tokens,
            top_score=max(best_scores) if best_scores else top_score,
            error=";".join(
                name
                for name, stats in source_stats.items()
                if _error_count(stats) > 0
            ) or None,
            context_chars=len(context),
        )
        return context
