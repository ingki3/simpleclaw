"""BIZ-425 — metadata 기반 read-only capability router 테스트."""

from __future__ import annotations

import pytest

from simpleclaw.agent.capability_router import (
    infer_domains,
    infer_intents,
    select_capability,
)
from simpleclaw.capability import CapabilityMetadata
from simpleclaw.recipes.models import RecipeDefinition
from simpleclaw.skills.models import SkillDefinition


@pytest.fixture
def fake_skill():
    def _make(
        name: str,
        *,
        domains: tuple[str, ...] = (),
        intents: tuple[str, ...] = (),
        read_only: bool = False,
        side_effects: bool = True,
        freshness_sensitive: bool = False,
        declared: bool = True,
    ) -> SkillDefinition:
        return SkillDefinition(
            name=name,
            description=f"{name} test skill",
            capability=CapabilityMetadata(
                domains=domains,
                intents=intents,
                read_only=read_only,
                side_effects=side_effects,
                freshness_sensitive=freshness_sensitive,
                declared=declared,
            ),
        )

    return _make


@pytest.fixture
def fake_recipe():
    def _make(
        name: str,
        *,
        domains: tuple[str, ...] = (),
        intents: tuple[str, ...] = (),
        read_only: bool = False,
        side_effects: bool = True,
        declared: bool = True,
    ) -> RecipeDefinition:
        return RecipeDefinition(
            name=name,
            description=f"{name} test recipe",
            capability=CapabilityMetadata(
                domains=domains,
                intents=intents,
                read_only=read_only,
                side_effects=side_effects,
                declared=declared,
            ),
        )

    return _make


def test_selects_read_only_standings_skill_for_normalized_question(fake_skill):
    skill = fake_skill(
        "sports-lookup-skill",
        domains=("sports",),
        intents=("standings", "current_result"),
        read_only=True,
        side_effects=False,
        freshness_sensitive=True,
    )
    decision = select_capability(
        "(직전 대화 맥락: 롯데, 야구) 그럼 현재 리그 순위표를 보여줘.",
        skills=[skill],
        recipes=[],
    )
    assert decision is not None
    assert decision.asset_type == "skill"
    assert decision.asset_name == "sports-lookup-skill"
    assert decision.safe_to_auto_execute is True
    assert "standings" in decision.matched_intents


def test_does_not_auto_select_side_effect_recipe(fake_recipe):
    recipe = fake_recipe(
        "create-reminder",
        intents=("standings",),  # 의도가 겹쳐도 부작용 자산은 후보 금지
        read_only=False,
        side_effects=True,
    )
    decision = select_capability(
        "현재 순위표 알려줘", skills=[], recipes=[recipe]
    )
    assert decision is None or decision.safe_to_auto_execute is False


def test_undeclared_capability_is_never_selected(fake_skill):
    skill = fake_skill(
        "legacy-skill",
        intents=("standings",),
        read_only=True,
        side_effects=False,
        declared=False,
    )
    decision = select_capability("현재 순위표 알려줘", skills=[skill], recipes=[])
    assert decision is None


def test_no_intent_match_returns_none(fake_skill):
    skill = fake_skill(
        "weather-skill",
        domains=("weather",),
        intents=("weather",),
        read_only=True,
        side_effects=False,
    )
    decision = select_capability(
        "이 코드 리팩토링 좀 도와줘", skills=[skill], recipes=[]
    )
    assert decision is None


def test_domain_mismatch_is_filtered(fake_skill):
    """자산과 질문 양쪽 도메인이 특정됐는데 서로 다르면 매칭하지 않는다."""
    market_skill = fake_skill(
        "market-quote-skill",
        domains=("market",),
        intents=("standings", "quote"),
        read_only=True,
        side_effects=False,
    )
    decision = select_capability(
        "(직전 대화 맥락: 롯데, 야구) 그럼 현재 순위는?",
        skills=[market_skill],
        recipes=[],
    )
    assert decision is None


def test_best_scoring_asset_wins(fake_skill, fake_recipe):
    generic = fake_skill(
        "generic-lookup",
        intents=("standings",),
        read_only=True,
        side_effects=False,
    )
    sports = fake_skill(
        "sports-lookup-skill",
        domains=("sports",),
        intents=("standings", "current_result"),
        read_only=True,
        side_effects=False,
        freshness_sensitive=True,
    )
    decision = select_capability(
        "(직전 대화 맥락: 롯데, 야구) 그럼 현재 순위와 오늘 결과는?",
        skills=[generic, sports],
        recipes=[],
    )
    assert decision is not None
    assert decision.asset_name == "sports-lookup-skill"


def test_timeliness_cue_alone_does_not_match(fake_skill):
    """realtime_lookup(현재/지금) cue 만으로는 자산을 특정하지 않는다."""
    skill = fake_skill(
        "sports-lookup-skill",
        domains=("sports",),
        intents=("realtime_lookup",),
        read_only=True,
        side_effects=False,
    )
    decision = select_capability("지금 뭐 하고 있어?", skills=[skill], recipes=[])
    assert decision is None


def test_infer_intents_and_domains_are_generic():
    assert "standings" in infer_intents("현재 리그 순위표 보여줘")
    assert "quote" in infer_intents("오늘 삼성전자 주가 얼마야?")
    assert "sports" in infer_domains("어제 야구 경기 결과")
    assert "market" in infer_domains("코스피 지수 알려줘")
    assert infer_intents("") == ()
    assert infer_domains("") == ()
