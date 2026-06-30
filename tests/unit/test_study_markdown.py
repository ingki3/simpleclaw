"""Study Wiki Markdown 직렬화 단위 테스트.

source of truth 가 Markdown 이므로, render→parse 왕복에서 모든 섹션·출처 필드가
보존되는지와 사람이 손으로 쓴 페이지를 관대하게 읽는지를 검증한다.
"""

from __future__ import annotations

from pathlib import Path

from simpleclaw.study.markdown import parse_study_page, render_study_page
from simpleclaw.study.types import StudyPage, StudySource


def test_render_and_parse_study_page_round_trip(tmp_path: Path):
    page = StudyPage(
        topic_id="ai-industry-openai",
        path=tmp_path / "openai.md",
        title="OpenAI",
        current_state=["IPO 일정은 확정이 아니라 보도 단계로 분리해야 한다."],
        sources=[StudySource(title="Example", url="https://example.com", confidence=0.8)],
        updated_at="2026-06-29T06:30:00+09:00",
    )

    text = render_study_page(page)
    parsed = parse_study_page(text, page.path)

    assert parsed.topic_id == "ai-industry-openai"
    assert parsed.title == "OpenAI"
    assert parsed.current_state
    assert parsed.sources[0].url == "https://example.com"


def test_round_trip_preserves_all_sections(tmp_path: Path):
    page = StudyPage(
        topic_id="ai-industry-openai",
        path=tmp_path / "openai.md",
        title="OpenAI",
        summary="OpenAI 는 생성형 AI 시장의 대표 기업이다.",
        current_state=["GPT 계열 모델을 운영한다.", "기업 가치 보도가 이어진다."],
        historical_context=["2015년 비영리로 설립."],
        personal_relevance=["형님은 AI 산업 동향에 관심이 많다."],
        answer_guidance=["보도와 확정 사실을 구분해 답할 것."],
        open_questions=["IPO 시점은 언제인가?"],
        sources=[
            StudySource(
                title="매일경제",
                url="https://mk.co.kr/article",
                published_at="2026-06-26",
                confidence=0.72,
            ),
            StudySource(title="Reuters", url="https://reuters.com/x"),
        ],
        updated_at="2026-06-29T06:30:00+09:00",
    )

    parsed = parse_study_page(render_study_page(page), page.path)

    assert parsed.summary == page.summary
    assert parsed.current_state == page.current_state
    assert parsed.historical_context == page.historical_context
    assert parsed.personal_relevance == page.personal_relevance
    assert parsed.answer_guidance == page.answer_guidance
    assert parsed.open_questions == page.open_questions
    assert parsed.updated_at == page.updated_at

    assert len(parsed.sources) == 2
    first = parsed.sources[0]
    assert first.title == "매일경제"
    assert first.url == "https://mk.co.kr/article"
    assert first.published_at == "2026-06-26"
    assert abs(first.confidence - 0.72) < 1e-9
    # 발행일·신뢰도가 없던 출처는 기본값으로 돌아온다.
    assert parsed.sources[1].title == "Reuters"
    assert parsed.sources[1].published_at is None
    assert parsed.sources[1].confidence == 0.0


def test_parse_tolerates_hand_written_page(tmp_path: Path):
    # frontmatter 의 일부만 있고 섹션 순서가 다른, 손으로 쓴 페이지.
    text = """---
topic_id: hand-written
title: 손으로 쓴 문서
---

# 손으로 쓴 문서

자유롭게 적은 요약 문단.

## 현재 상태
- 첫 줄
* 별표 불릿도 허용

## Sources
- https://example.org/only-url
"""
    parsed = parse_study_page(text, tmp_path / "hand.md")

    assert parsed.topic_id == "hand-written"
    assert parsed.title == "손으로 쓴 문서"
    assert parsed.summary == "자유롭게 적은 요약 문단."
    assert parsed.current_state == ["첫 줄", "별표 불릿도 허용"]
    # URL 만 있는 줄도 출처로 인식하고, 제목은 URL 로 대체한다.
    assert len(parsed.sources) == 1
    assert parsed.sources[0].url == "https://example.org/only-url"


def test_parse_without_frontmatter_recovers_title_from_h1(tmp_path: Path):
    text = "# 제목만 있는 문서\n\n## 현재 상태\n- 항목\n"
    parsed = parse_study_page(text, tmp_path / "x.md")
    assert parsed.title == "제목만 있는 문서"
    assert parsed.topic_id == ""
    assert parsed.current_state == ["항목"]
