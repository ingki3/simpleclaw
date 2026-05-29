"""asset selector guardrail 동작을 검증한다.

BIZ-310은 selector를 최종 실행 결정자가 아니라 top-k 후보 축소기로만 사용한다.
따라서 테스트는 LLM 응답을 신뢰하기보다 recipe 과선택과 ambiguous intent를
보수적으로 fallback 처리하는 정책을 고정한다.
"""

from __future__ import annotations

from simpleclaw.agent.asset_selector import (
    AssetCandidate,
    SelectorAsset,
    normalize_selector_response,
)
from simpleclaw.config import load_asset_selection_config
from simpleclaw.llm.models import ToolCall


KNOWN_ASSETS = [
    SelectorAsset(type="skill", name="news-search-skill", description="Search recent news."),
    SelectorAsset(type="skill", name="gmail-skill", description="Search and summarize email."),
    SelectorAsset(type="recipe", name="ai-report", description="Send a scheduled AI news briefing."),
    SelectorAsset(type="recipe", name="krstock", description="Summarize Korean stock market after close."),
]


def _tool_call(selected: list[dict], *, fallback: bool = False, fallback_reason: str = "") -> list[ToolCall]:
    """테스트에서 selector function-call 응답을 짧게 만든다."""
    return [
        ToolCall(
            id="call-1",
            name="select_assets",
            arguments={
                "selected": selected,
                "fallback": fallback,
                "fallback_reason": fallback_reason,
            },
        )
    ]


def test_explicit_recipe_activation_keeps_recipe_candidate() -> None:
    """명시적 실행/브리핑 요청이면 recipe 후보를 top-k에 유지한다."""
    result = normalize_selector_response(
        user_message="매일 아침 최신 AI 뉴스 브리핑을 보내줘",
        known_assets=KNOWN_ASSETS,
        tool_calls=_tool_call(
            [
                {"type": "recipe", "name": "ai-report", "confidence": 0.91, "reason": "scheduled briefing"},
                {"type": "skill", "name": "news-search-skill", "confidence": 0.72, "reason": "news source"},
            ]
        ),
    )

    assert result.selected == [
        AssetCandidate(type="recipe", name="ai-report", confidence=0.91, reason="scheduled briefing"),
        AssetCandidate(type="skill", name="news-search-skill", confidence=0.72, reason="news source"),
    ]
    assert result.fallback_required is False


def test_ad_hoc_news_summary_drops_recipe_overselection_and_preserves_skill() -> None:
    """ad-hoc 뉴스 검색/요약은 recipe 과선택만 제거하고 skill 후보는 보존한다."""
    result = normalize_selector_response(
        user_message="최신 AI 뉴스 검색해서 사실 확인을 보강해줘",
        known_assets=KNOWN_ASSETS,
        tool_calls=_tool_call(
            [
                {"type": "recipe", "name": "ai-report", "confidence": 0.95, "reason": "AI news"},
                {"type": "skill", "name": "news-search-skill", "confidence": 0.86, "reason": "search news"},
            ]
        ),
    )

    assert result.selected == [
        AssetCandidate(type="skill", name="news-search-skill", confidence=0.86, reason="search news")
    ]
    assert result.fallback_required is False
    assert "recipe_guardrail" in result.fallback_reason


def test_ambiguous_request_drops_recipe_and_requires_fallback() -> None:
    """모호한 요청은 recipe 후보를 제거하고 main LLM fallback이 필요하다고 표시한다."""
    result = normalize_selector_response(
        user_message="이거 좀 정리해줘",
        known_assets=KNOWN_ASSETS,
        tool_calls=_tool_call(
            [{"type": "recipe", "name": "krstock", "confidence": 0.88, "reason": "summarize"}]
        ),
    )

    assert result.selected == []
    assert result.fallback_required is True
    assert "ambiguous_intent" in result.fallback_reason


def test_missing_function_call_or_empty_selection_requires_fallback() -> None:
    """function-call 누락 또는 빈 selection은 selector 단독 결정을 금지한다."""
    missing_call = normalize_selector_response(
        user_message="안 읽은 메일을 검색해서 중요한 것만 요약해줘",
        known_assets=KNOWN_ASSETS,
        response_text='{"selected": [{"type": "skill", "name": "gmail-skill", "confidence": 0.9, "reason": "mail"}]}',
        tool_calls=None,
    )
    empty_selection = normalize_selector_response(
        user_message="안 읽은 메일을 검색해서 중요한 것만 요약해줘",
        known_assets=KNOWN_ASSETS,
        tool_calls=_tool_call([]),
    )

    assert missing_call.fallback_required is True
    assert "missing_function_call" in missing_call.fallback_reason
    assert empty_selection.fallback_required is True
    assert "empty_selection" in empty_selection.fallback_reason


def test_asset_selection_config_defaults_disabled(tmp_path) -> None:
    """asset_selection 누락 시 운영 기본값은 disabled이다."""
    config = tmp_path / "config.yaml"
    config.write_text("llm: {}\n")

    loaded = load_asset_selection_config(config)

    assert loaded["enabled"] is False
    assert loaded["backend"] == "gemini"
    assert loaded["skill_top_k"] > 0
    assert loaded["recipe_top_k"] > 0


def test_asset_selection_config_overrides_and_clamps(tmp_path) -> None:
    """config override는 타입을 정규화하고 confidence 범위를 clamp한다."""
    config = tmp_path / "config.yaml"
    config.write_text(
        """
asset_selection:
  enabled: true
  backend: gemini-fast
  skill_top_k: "2"
  recipe_top_k: -1
  min_confidence: 2.5
  bypass_below_count: "3"
  fallback_top_k: 0
  max_tokens: "128"
"""
    )

    loaded = load_asset_selection_config(config)

    assert loaded == {
        "enabled": True,
        "backend": "gemini-fast",
        "skill_top_k": 2,
        "recipe_top_k": 0,
        "min_confidence": 1.0,
        "bypass_below_count": 3,
        "fallback_top_k": 1,
        "max_tokens": 128,
    }
