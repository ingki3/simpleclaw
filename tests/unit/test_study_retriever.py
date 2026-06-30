"""BIZ-393 — Agent Study Wiki retriever 단위 테스트.

retriever 는 wiki on-disk 레이아웃(topics.yaml + topics/<id>.md)을 읽어 질문과
관련된 배경지식 블록을 만든다. 여기서는 (1) 매칭/랭킹, (2) 블록 포맷과 경고,
(3) 결정적 예산 절단, (4) 실패 격리, (5) archived/historical 정책을 고정한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from simpleclaw.study.markdown import render_study_page
from simpleclaw.study.paths import init_wiki_root, topic_page_path
from simpleclaw.study.retriever import (
    STUDY_CONTEXT_HEADER,
    StudyRetrievalConfig,
    StudyRetriever,
)
from simpleclaw.study.topic_registry import TopicRegistry
from simpleclaw.study.types import StudyPage, StudySource, StudyTopic


def _build_wiki(tmp_path: Path) -> Path:
    """OpenAI/월드컵 두 topic 과 page 를 가진 테스트 wiki 를 만든다."""
    root = tmp_path / "wiki"
    init_wiki_root(root)

    registry = TopicRegistry(path=root / "topics.yaml")
    registry.upsert(
        StudyTopic(
            id="ai-industry-openai",
            label="OpenAI / AI industry",
            description="OpenAI 상장과 AI 산업 동향",
            tags=["ai", "openai", "증시"],
            status="active",
            updated_at="2026-06-29T06:30:00+09:00",
        )
    )
    registry.upsert(
        StudyTopic(
            id="korea-football",
            label="대한민국 축구 월드컵",
            description="월드컵 진출 경우의 수",
            tags=["축구", "월드컵"],
            status="active",
            updated_at="2026-06-20T06:30:00+09:00",
        )
    )
    registry.save()

    openai_page = StudyPage(
        topic_id="ai-industry-openai",
        path=topic_page_path("ai-industry-openai", base=root),
        title="OpenAI / AI industry",
        current_state=["OpenAI 상장(IPO) 시점은 확정이 아니라 보도 단계다."],
        answer_guidance=["증시 영향은 확정 결과가 아니라 가능한 경로로 설명한다."],
        sources=[
            StudySource(
                title="Example",
                url="https://example.com/openai-ipo",
                confidence=0.72,
            )
        ],
        updated_at="2026-06-29T06:30:00+09:00",
    )
    openai_page.path.write_text(render_study_page(openai_page), encoding="utf-8")

    football_page = StudyPage(
        topic_id="korea-football",
        path=topic_page_path("korea-football", base=root),
        title="대한민국 축구 월드컵",
        current_state=["남은 경기와 조 순위에 따라 경우의 수가 달라진다."],
        updated_at="2026-06-20T06:30:00+09:00",
    )
    football_page.path.write_text(render_study_page(football_page), encoding="utf-8")

    return root


def _retriever(root: Path, **overrides) -> StudyRetriever:
    cfg = StudyRetrievalConfig(
        enabled=overrides.pop("enabled", True),
        wiki_dir=root,
        top_k=overrides.pop("top_k", 4),
        max_context_chars=overrides.pop("max_context_chars", 5000),
    )
    return StudyRetriever(cfg)


def test_retrieve_returns_relevant_topic_block(tmp_path: Path):
    """질문 토큰과 겹치는 topic 이 헤더 + 노트 + 출처와 함께 회수된다."""
    root = _build_wiki(tmp_path)
    retriever = _retriever(root)

    context = retriever.retrieve_context("OpenAI 상장 연기가 증시에 끼치는 영향")

    assert STUDY_CONTEXT_HEADER in context
    assert "OpenAI" in context
    assert "보도 단계" in context  # 관련 노트가 실린다
    assert "https://example.com/openai-ipo" in context
    assert "Confidence: 0.72" in context


def test_block_includes_not_user_memory_warning(tmp_path: Path):
    """블록에 '사용자 메모리가 아님 + 현재성 재확인' 경고가 포함된다."""
    root = _build_wiki(tmp_path)
    context = _retriever(root).retrieve_context("OpenAI 증시 영향")

    assert "사용자 메모리" in context
    assert "It is not a user profile fact" in context
    # 사용자 프로필 블록으로 오인되지 않도록 해당 연속 문자열은 없어야 한다.
    assert "사용자 프로필" not in context


def test_unrelated_question_returns_empty(tmp_path: Path):
    """어떤 topic 과도 겹치지 않는 질문은 빈 문자열을 돌려준다."""
    root = _build_wiki(tmp_path)
    context = _retriever(root).retrieve_context("오늘 점심 메뉴 추천해줘")

    assert context == ""


def test_disabled_retriever_returns_empty(tmp_path: Path):
    """기능이 꺼져 있으면 항상 빈 문자열."""
    root = _build_wiki(tmp_path)
    context = _retriever(root, enabled=False).retrieve_context("OpenAI 증시 영향")

    assert context == ""


def test_top_k_caps_topic_count(tmp_path: Path):
    """top_k=1 이면 가장 관련 높은 topic 한 건만 싣는다."""
    root = _build_wiki(tmp_path)
    # 두 topic 모두 건드리는 질문이라도 top_k 로 잘린다.
    context = _retriever(root, top_k=1).retrieve_context("OpenAI 상장과 월드컵 축구")

    assert context.count("- Topic:") == 1


def test_budget_truncation_is_deterministic(tmp_path: Path):
    """max_context_chars 를 넘기지 않고, 같은 입력은 같은 출력을 낸다."""
    root = _build_wiki(tmp_path)
    retriever = _retriever(root, max_context_chars=400)

    query = "OpenAI 상장과 월드컵 축구 경우의 수"
    first = retriever.retrieve_context(query)
    second = retriever.retrieve_context(query)

    assert first == second
    assert len(first) <= 400
    assert STUDY_CONTEXT_HEADER in first  # 헤더는 항상 유지


def test_missing_wiki_dir_returns_empty(tmp_path: Path):
    """wiki 디렉터리가 아예 없어도 예외 없이 빈 문자열."""
    retriever = _retriever(tmp_path / "does-not-exist")
    assert retriever.retrieve_context("OpenAI 증시 영향") == ""


def test_corrupt_topics_yaml_is_isolated(tmp_path: Path):
    """topics.yaml 이 깨져도 예외를 던지지 않고 빈 문자열로 격리된다."""
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "topics.yaml").write_text("topics: [::: not yaml", encoding="utf-8")

    retriever = _retriever(root)
    # load_topics 는 깨진 파일을 빈 목록으로 처리하므로 결과는 빈 문자열.
    assert retriever.retrieve_context("OpenAI 증시 영향") == ""


def test_archived_topic_excluded_unless_historical(tmp_path: Path):
    """archived topic 은 일반 질문에서 제외되고 historical=True 일 때만 회수된다."""
    root = tmp_path / "wiki"
    init_wiki_root(root)
    registry = TopicRegistry(path=root / "topics.yaml")
    registry.upsert(
        StudyTopic(
            id="old-fad",
            label="옛날 유행 NFT",
            description="NFT 열풍 배경",
            tags=["nft"],
            status="archived",
            updated_at="2025-01-01T00:00:00+09:00",
        )
    )
    registry.save()
    page = StudyPage(
        topic_id="old-fad",
        path=topic_page_path("old-fad", base=root),
        title="옛날 유행 NFT",
        historical_context=["NFT 열풍은 2021년 정점을 찍었다."],
    )
    page.path.write_text(render_study_page(page), encoding="utf-8")

    retriever = _retriever(root)
    assert retriever.retrieve_context("NFT 열풍") == ""
    historical = retriever.retrieve_context("NFT 열풍", historical=True)
    assert "옛날 유행 NFT" in historical


def test_empty_query_returns_empty(tmp_path: Path):
    """빈/공백 질문은 빈 문자열."""
    root = _build_wiki(tmp_path)
    assert _retriever(root).retrieve_context("   ") == ""


@pytest.mark.parametrize("status,present", [("pinned", True), ("active", True)])
def test_topic_without_page_still_matches_via_metadata(
    tmp_path: Path, status: str, present: bool
):
    """page 파일이 없어도 label/tags 메타로 매칭되면 회수된다."""
    root = tmp_path / "wiki"
    init_wiki_root(root)
    registry = TopicRegistry(path=root / "topics.yaml")
    registry.upsert(
        StudyTopic(
            id="markets-ai",
            label="AI 관련주 증시",
            tags=["증시", "ai"],
            status=status,
        )
    )
    registry.save()  # page 파일은 만들지 않는다

    context = _retriever(root).retrieve_context("AI 증시 전망")
    assert ("AI 관련주 증시" in context) is present
