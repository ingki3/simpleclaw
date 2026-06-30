"""BIZ-393 — ContextRetrievalService 의 Agent Study Wiki 통합 테스트.

핵심 계약:
- ``retrieve()`` 결과에 study context 블록이 합쳐진다.
- study 회수는 대화 RAG/임베딩 활성 여부와 무관하게 동작한다(독립 실패 격리).
- study 회수가 던진 예외는 대화 응답으로 새지 않는다(빈 study 로 격리).
- study 결과는 사용자 프로필/메모리 블록으로 오인되지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from simpleclaw.agent.context_retrieval import (
    ContextRetrievalConfig,
    ContextRetrievalService,
    annotate_study_freshness,
)


def _config() -> ContextRetrievalConfig:
    """RAG/장기기억은 모두 끈 최소 설정 — study 경로만 검증하기 위함."""
    return ContextRetrievalConfig(
        rag_top_k=3,
        rag_threshold=0.5,
        long_term_enabled=False,
        long_term_top_k=3,
        long_term_min_confidence=0.7,
        long_term_promotion_threshold=3,
        long_term_context_budget_chars=1600,
        long_term_per_item_chars=400,
        long_term_insights_file="/tmp/insights.jsonl",
        long_term_active_projects_file="/tmp/projects.jsonl",
        long_term_active_projects_window_days=7,
    )


class FakeStudyRetriever:
    """질문에 OpenAI 가 들어가면 study 블록을 돌려주는 테스트 retriever."""

    def __init__(self, enabled: bool = True, *, raises: bool = False):
        self.enabled = enabled
        self._raises = raises
        self.calls: list[str] = []

    def retrieve_context(self, user_text: str, *, historical: bool = False) -> str:
        self.calls.append(user_text)
        if self._raises:
            raise RuntimeError("study store exploded")
        if "OpenAI" in user_text:
            return (
                "## Agent Study Context\n"
                "Purpose: The following is background knowledge the agent studied "
                "for this user. It is not a user profile fact. Verify live/current "
                "facts when needed.\n"
                "참고: 아래는 외부 배경지식이며 사용자 메모리가 아닙니다.\n\n"
                "- Topic: OpenAI / AI industry\n"
                "  Relevant notes:\n"
                "  - OpenAI IPO timing is reported, not confirmed."
            )
        return ""


@pytest.mark.asyncio
async def test_context_retrieval_includes_agent_study_block():
    """RAG 임베딩이 꺼져 있어도 study 블록은 회수돼 결과에 포함된다."""
    service = ContextRetrievalService(
        store=None,
        embedding_service=None,  # RAG 비활성 — study 는 독립적으로 동작해야 한다
        config=_config(),
        structured_logger=None,
        study_retriever=FakeStudyRetriever(),
    )

    context = await service.retrieve("OpenAI 상장 연기가 증시에 끼치는 영향")

    assert "## Agent Study Context" in context
    assert "사용자 프로필" not in context
    assert "OpenAI" in context


@pytest.mark.asyncio
async def test_no_study_retriever_yields_plain_rag_result():
    """study retriever 가 없으면(기존 동작) study 블록 없이 빈 문자열."""
    service = ContextRetrievalService(
        store=None,
        embedding_service=None,
        config=_config(),
        structured_logger=None,
    )

    context = await service.retrieve("OpenAI 상장 연기")
    assert context == ""


@pytest.mark.asyncio
async def test_disabled_study_retriever_is_skipped():
    """비활성 retriever 는 호출되지 않고 빈 결과."""
    fake = FakeStudyRetriever(enabled=False)
    service = ContextRetrievalService(
        store=None,
        embedding_service=None,
        config=_config(),
        structured_logger=None,
        study_retriever=fake,
    )

    context = await service.retrieve("OpenAI 상장 연기")
    assert context == ""
    assert fake.calls == []  # enabled=False 면 retrieve_context 를 부르지 않는다


@pytest.mark.asyncio
async def test_study_failure_is_isolated_from_conversation_flow():
    """study 회수가 예외를 던져도 retrieve() 는 빈 문자열로 안전하게 끝난다."""
    service = ContextRetrievalService(
        store=None,
        embedding_service=None,
        config=_config(),
        structured_logger=None,
        study_retriever=FakeStudyRetriever(raises=True),
    )

    # 예외가 위로 전파되지 않아야 한다.
    context = await service.retrieve("OpenAI 상장 연기")
    assert context == ""


# --------------------------------------------------------------------------
# BIZ-394 — study context freshness/confidence gate
# --------------------------------------------------------------------------

_NOW = datetime(2026, 6, 30, tzinfo=timezone.utc)


def test_annotate_study_freshness_flags_stale_block():
    """마지막 갱신이 임계일을 넘긴 블록에는 stale 마커와 한계 강제 지시가 붙는다."""
    block = (
        "## Agent Study Context\n"
        "- Topic: OpenAI\n"
        "  Updated: 2026-05-01\n"
        "  Confidence: 0.90"
    )
    annotated = annotate_study_freshness(block, now=_NOW, stale_after_days=14)
    assert "Freshness: stale" in annotated
    assert "한계" in annotated
    assert block in annotated  # 원본 보존


def test_annotate_study_freshness_flags_low_confidence_block():
    """confidence 최솟값이 임계 미만이면 갱신이 최신이어도 경고가 붙는다."""
    block = (
        "## Agent Study Context\n"
        "- Topic: OpenAI\n"
        "  Updated: 2026-06-29\n"
        "  Confidence: 0.30"
    )
    annotated = annotate_study_freshness(
        block, now=_NOW, stale_after_days=14, min_confidence=0.5
    )
    assert "Freshness: stale" in annotated


def test_annotate_study_freshness_keeps_fresh_block_untouched():
    """최신 + 고신뢰 블록은 경고 없이 그대로 둔다."""
    block = (
        "## Agent Study Context\n"
        "- Topic: OpenAI\n"
        "  Updated: 2026-06-29\n"
        "  Confidence: 0.95"
    )
    annotated = annotate_study_freshness(
        block, now=_NOW, stale_after_days=14, min_confidence=0.5
    )
    assert annotated == block
    assert "Freshness: stale" not in annotated


def test_annotate_study_freshness_no_meta_is_untouched():
    """Updated/Confidence 메타가 전혀 없으면 근거 없는 stale 판정을 하지 않는다."""
    block = "## Agent Study Context\n- Topic: OpenAI\n  Relevant notes:\n  - 배경"
    annotated = annotate_study_freshness(block, now=_NOW)
    assert annotated == block


class _DatedStudyRetriever:
    """오래된 Updated/낮은 Confidence 블록을 돌려주는 테스트 retriever."""

    enabled = True

    def retrieve_context(self, user_text: str, *, historical: bool = False) -> str:
        return (
            "## Agent Study Context\n"
            "- Topic: OpenAI\n"
            "  Updated: 2026-04-01\n"
            "  Confidence: 0.40"
        )


@pytest.mark.asyncio
async def test_retrieve_injects_freshness_warning_for_stale_study():
    """서비스 retrieve() 가 stale study 블록에 신선도 경고를 주입한다."""
    service = ContextRetrievalService(
        store=None,
        embedding_service=None,
        config=_config(),
        structured_logger=None,
        study_retriever=_DatedStudyRetriever(),
        now_provider=lambda: _NOW,
    )

    context = await service.retrieve("OpenAI 지금 상황 어때?")
    assert "Freshness: stale" in context
    assert "실시간 조회" in context
