"""Agent Study Wiki — end-to-end 회귀 고정(fixtures).

이 파일의 목적은 "문서를 만들었다"가 아니라 *실제 실패 유형이 줄어드는 것*이다.
BIZ-396(Issue 11)은 그동안 반복적으로 문제가 됐던 4가지 시나리오를 회귀 테스트로
고정한다.

- Case A — 월드컵 경우의 수: 스포츠 topic 은 타임라인 검증이 강제돼야 하고,
  타임라인 검증 불가 결과는 신뢰가 깎여야 한다.
- Case B — OpenAI IPO 영향: ai-industry/markets topic 이 적절한 collector 로 계획되고,
  모든 fetch 요청이 freshness gate 입력(`freshness_hours`)을 실어야 하며, 저신뢰
  general_news 는 "보도/추정/확정"을 분리해 wiki 채택에서 걸러져야 한다.
- Case C — Dreaming 에서 새 topic 생성: 동적으로 생긴 `coding-agents` topic 도 일단
  존재하면 source planner 가 정상적으로 수집 계획에 태운다.
- Case D — 일반 뉴스 중요도: 형님 관심사(AI/시장)와 관련 높은 뉴스는 채택되고,
  무관한 지역 축제는 relevance gate 에서 제외된다.

설계 메모(왜 일부는 xfail 인가):
    이 회귀는 study source planner(BIZ-391)까지가 머지된 시점에 작성됐다. 각 Case 의
    "대화 context 검색 → route 선택 → 답변 contract" 같은 *full e2e* 계약은 아직
    머지되지 않은 후속 issue(BIZ-387/389/390/393/394)에 의존한다. 따라서 *지금 코드가
    보장하는 불변식*은 실제 통과 테스트로 고정하고, 아직 구현되지 않은 e2e 계약은
    `xfail(strict=False)` 로 표시해 의도를 박제한다. 후속 issue 가 머지되면 해당
    테스트는 XPASS 로 떠올라 "이제 마커를 떼고 정식 회귀로 승격하라"는 신호가 된다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest

from simpleclaw.study.collectors import (
    CollectorRegistry,
    StudyFetchRequest,
    StudyFetchResult,
)
from simpleclaw.study.source_planner import (
    DEFAULT_SOURCE_POLICY,
    ConfidenceRelevanceScorer,
    RelevanceAssessment,
    TopicKind,
    plan_fetch_requests,
    select_wiki_worthy,
)

# --------------------------------------------------------------------------- #
# 테스트 헬퍼 — StudyTopic Protocol 을 만족하는 topic 과 수집 결과 빌더.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StudyTopicStub:
    """`StudyTopic` Protocol 을 만족하는 회귀 테스트용 topic.

    선행 issue 의 실제 topic_registry 를 import 하지 않고 duck typing 으로만 의존해,
    registry 가 아직 머지되지 않아도 회귀가 깨지지 않게 한다.
    """

    topic_id: str
    label: str
    category: str
    kind: TopicKind = TopicKind.USER_INTEREST
    max_sources: int = 3
    freshness_hours: int = 24


def _result(
    topic_id: str,
    *,
    title: str = "제목",
    text: str = "본문",
    confidence: float = 0.9,
    limitations: tuple[str, ...] = (),
    collector: str = "news-search-skill",
    query: str = "q",
) -> StudyFetchResult:
    """`StudyFetchResult` 빌더. 각 Case 가 필요한 필드만 지정한다."""
    request = StudyFetchRequest(topic_id=topic_id, query=query, collector=collector)
    return StudyFetchResult(
        request=request,
        title=title,
        text=text,
        confidence=confidence,
        limitations=limitations,
    )


@dataclass
class StubCollector:
    """등록된 결과를 그대로 돌려주는 테스트 collector.

    실제 외부 도구 호출 없이 plan → fetch → select 파이프라인을 end-to-end 로
    돌리기 위한 stub. `name` 으로 source policy 의 collector 이름과 매칭된다.
    """

    name: str
    results: list[StudyFetchResult] = field(default_factory=list)
    calls: list[StudyFetchRequest] = field(default_factory=list)

    def fetch(self, request: StudyFetchRequest) -> Sequence[StudyFetchResult]:
        self.calls.append(request)
        # 요청한 topic 의 결과만 반환(실제 collector 의 topic 격리 모사).
        return [r for r in self.results if r.request.topic_id == request.topic_id]


@dataclass
class InterestAwareScorer:
    """형님 관심사 category 와의 관련도로 점수를 매기는 결정적 scorer.

    실제 후속 issue 의 LLM `news_relevance` scorer 를 대체하는 회귀용 stub.
    관심 category(여기서는 ai-industry/markets)면 높은 점수, 그 외 지역/생활
    뉴스는 낮은 점수를 줘 relevance gate 동작을 결정적으로 검증한다.
    """

    interest_categories: frozenset[str]

    def score(
        self, result: StudyFetchResult, *, topic: StudyTopicStub
    ) -> RelevanceAssessment:
        if topic is not None and topic.category in self.interest_categories:
            return RelevanceAssessment(
                score=0.92,
                should_study=True,
                reasons=(f"관심 category {topic.category} 와 직접 관련",),
            )
        return RelevanceAssessment(
            score=0.1,
            should_study=False,
            reasons=("형님 관심사와 무관한 지역/생활 뉴스",),
        )


# --------------------------------------------------------------------------- #
# Case A — 월드컵 경우의 수 (스포츠 topic = 타임라인 검증 강제)
# --------------------------------------------------------------------------- #


class TestCaseAWorldCup:
    """`대한민국 월드컵 32강 진출 가능성` 류 질문의 회귀 고정.

    핵심 실패 유형: 스포츠 같은 빠르게 변하는 사실을 타임라인 검증 없이 단정.
    지금 코드가 보장하는 불변식은 (1) 스포츠 topic 이 realtime/web 검색 collector 로
    계획되고, (2) source policy 가 타임라인 검증을 강제 표시하며, (3) 타임라인 검증
    불가 결과는 신뢰가 깎인다는 것이다.
    """

    def test_world_cup_topic_plans_realtime_collectors(self):
        topic = StudyTopicStub(
            "world-cup-2026",
            "대한민국 월드컵 32강 진출 가능성",
            "sports",
            freshness_hours=6,
        )

        requests = plan_fetch_requests([topic])

        # 스포츠는 realtime-lookup 우선 → web_search 폴백 순서.
        assert [r.collector for r in requests] == ["realtime-lookup-skill", "web_search"]
        # freshness gate 입력이 모든 요청에 전파된다(현재성 질문이므로 짧은 윈도).
        assert all(r.freshness_hours == 6 for r in requests)
        assert all(r.query == topic.label for r in requests)

    def test_sports_category_requires_timeline_validation(self):
        # "current fact guarded" 라우팅이 의존하는 구조적 가드.
        policy = DEFAULT_SOURCE_POLICY.for_category("sports")
        assert policy.require_timeline_validation is True

    def test_timeline_unverifiable_result_is_penalized_and_rejected(self):
        # 경우의 수처럼 검증 불가한 일반 뉴스는 신뢰가 깎여 wiki 에 박히지 않는다.
        topic = StudyTopicStub(
            "world-cup-2026",
            "월드컵 경우의 수",
            "sports",
            kind=TopicKind.GENERAL_NEWS,
        )
        result = _result(
            "world-cup-2026",
            confidence=0.6,
            limitations=("타임라인 검증 불가",),
            collector="web_search",
        )

        selection = select_wiki_worthy(
            [result], topics={"world-cup-2026": topic}, threshold=0.5
        )

        assert selection.selected == ()
        assert len(selection.rejected) == 1
        _, assessment = selection.rejected[0]
        assert assessment.score < 0.5
        assert any("한계" in reason for reason in assessment.reasons)

    @pytest.mark.xfail(
        reason="대화 context 검색 + complex/current-fact 라우팅 + 답변 contract 는 "
        "BIZ-393(retrieval→context)/BIZ-394(freshness gate + complex routing) 머지 후 활성화",
        strict=False,
    )
    def test_world_cup_question_routes_to_guarded_contract(self):
        # 후속 e2e 계약: study context 에서 world-cup-2026/korea-football topic 이
        # 검색되고, route 가 complex workflow 또는 current-fact-guarded 로 가며,
        # 답변 contract 가 현재 상태 + 규칙 + 남은 변수 + 계산을 요구한다.
        from simpleclaw.study import retrieval

        question = "대한민국 월드컵 32강 진출 가능성이 어떻게 되지?"
        context = retrieval.retrieve_study_context(question)
        topic_ids = {t.topic_id for t in context.topics}
        assert {"world-cup-2026", "korea-football"} & topic_ids
        assert context.route in {"complex_workflow", "current_fact_guarded"}
        assert context.answer_contract.requires_current_state
        assert context.answer_contract.requires_remaining_variables
        assert context.answer_contract.requires_calculation


# --------------------------------------------------------------------------- #
# Case B — OpenAI IPO 영향 (ai-industry + markets, freshness, 보도/추정/확정 분리)
# --------------------------------------------------------------------------- #


class TestCaseBOpenAiIpo:
    """`OpenAI 상장 연기가 증시에 끼치는 영향` 류 질문의 회귀 고정.

    핵심 실패 유형: (1) 현재성 확인 없이 옛 정보로 답하거나, (2) 보도/추정/확정을
    뭉뚱그려 단정. 지금 코드가 보장하는 불변식은 ai-industry/markets topic 이
    적절한 collector 로 계획되고, 모든 요청이 freshness gate 입력을 싣고, 저신뢰
    general_news 가 relevance gate 에서 걸러진다는 것이다.
    """

    def test_openai_and_market_topics_plan_expected_collectors(self):
        openai_topic = StudyTopicStub("ai-industry/openai", "OpenAI 상장", "ai-industry")
        market_topic = StudyTopicStub("markets/ai-stocks", "AI 관련주 영향", "markets")

        requests = plan_fetch_requests([openai_topic, market_topic])
        by_topic: dict[str, list[str]] = {}
        for r in requests:
            by_topic.setdefault(r.topic_id, []).append(r.collector)

        assert by_topic["ai-industry/openai"] == ["news-search-skill", "web_search"]
        assert by_topic["markets/ai-stocks"] == [
            "us-stock-skill",
            "kr-stock-skill",
            "news-search-skill",
        ]

    def test_every_request_carries_freshness_gate_input(self):
        # freshness gate 는 요청에 실린 freshness_hours 를 기준으로 현재성을 강제한다.
        topic = StudyTopicStub(
            "ai-industry/openai", "OpenAI 상장", "ai-industry", freshness_hours=12
        )
        requests = plan_fetch_requests([topic])
        assert requests  # collector 계획이 비지 않는다.
        assert all(r.freshness_hours == 12 for r in requests)

    def test_low_confidence_report_is_separated_from_confirmed(self):
        # "보도/추정"(저신뢰 + 한계 표시)은 "확정"과 분리돼 wiki 채택에서 빠진다.
        topic = StudyTopicStub(
            "markets/ai-stocks", "상장 연기 영향", "markets", kind=TopicKind.GENERAL_NEWS
        )
        rumor = _result(
            "markets/ai-stocks",
            title="[속보] 상장 연기설",
            confidence=0.45,
            limitations=("단일 출처 보도", "확정 아님"),
        )
        confirmed = _result(
            "markets/ai-stocks",
            title="공식 공시",
            confidence=0.95,
        )

        selection = select_wiki_worthy(
            [rumor, confirmed],
            topics={"markets/ai-stocks": topic},
            scorer=ConfidenceRelevanceScorer(),
            threshold=0.5,
        )

        selected_titles = {r.title for r in selection.selected}
        rejected_titles = {r.title for r, _ in selection.rejected}
        assert "공식 공시" in selected_titles
        assert "[속보] 상장 연기설" in rejected_titles

    @pytest.mark.xfail(
        reason="현재성(freshness) 강제 확인과 보도/추정/확정 라벨링을 답변에 요구하는 "
        "계약은 BIZ-394(freshness/confidence gate) 머지 후 활성화",
        strict=False,
    )
    def test_openai_ipo_question_enforces_freshness_and_evidence_tiers(self):
        from simpleclaw.study import retrieval

        question = "OpenAI 상장 연기가 증시에 끼치는 영향을 조사해줘"
        context = retrieval.retrieve_study_context(question)
        topic_ids = {t.topic_id for t in context.topics}
        assert "ai-industry/openai" in topic_ids
        assert "markets/ai-stocks" in topic_ids
        assert context.freshness_gate.requires_current_check
        # 저신뢰 근거면 보도/추정/확정을 분리해야 한다.
        assert context.answer_contract.requires_evidence_tiering


# --------------------------------------------------------------------------- #
# Case C — Dreaming 에서 새 topic 생성 (coding-agents)
# --------------------------------------------------------------------------- #


class TestCaseCDreamingNewTopic:
    """반복 신호(Claude Code/Codex/Antigravity CLI)로 생긴 `coding-agents` topic 회귀.

    핵심 실패 유형: 동적으로 생긴 관심사가 수집 파이프라인에 안 태워짐. 지금 코드가
    보장하는 불변식은 *일단 topic 이 만들어지면* source planner 가 그것을 정상적으로
    수집 계획에 포함한다는 것이다(생성 자체는 후속 issue 의존 → xfail).
    """

    def test_dynamically_created_topic_flows_through_planner(self):
        # Dreaming 이 만들어낼 법한 topic 을 모사 — 일단 존재하면 계획에 포함돼야 한다.
        coding_agents = StudyTopicStub(
            "coding-agents",
            "Claude Code / Codex / Antigravity CLI 동향",
            "ai-industry",
        )

        requests = plan_fetch_requests([coding_agents])

        assert [r.collector for r in requests] == ["news-search-skill", "web_search"]
        assert all(r.topic_id == "coding-agents" for r in requests)

    @pytest.mark.xfail(
        reason="대화/Dreaming 신호에서 candidate topic 을 생성하고 daily planner 가 "
        "수집 대상으로 선택하는 흐름은 BIZ-389(signal 추출)/BIZ-390(topic registry) 머지 후 활성화",
        strict=False,
    )
    def test_repeated_signals_create_coding_agents_topic(self):
        from simpleclaw.study import interest_signals

        signals = [
            {"term": "Claude Code", "count": 4},
            {"term": "Codex", "count": 3},
            {"term": "Antigravity CLI", "count": 2},
        ]
        candidates = interest_signals.derive_candidate_topics(signals, window_days=3)
        topic_ids = {t.topic_id for t in candidates}
        assert "coding-agents" in topic_ids
        coding = next(t for t in candidates if t.topic_id == "coding-agents")
        assert coding.status in {"candidate", "active"}


# --------------------------------------------------------------------------- #
# Case D — 일반 뉴스 중요도 (관련 높은 AI/시장은 채택, 무관 지역 축제는 제외)
# --------------------------------------------------------------------------- #


class TestCaseDNewsImportance:
    """일반 뉴스 후보의 중요도 선별 회귀 고정.

    핵심 실패 유형: 형님 관심사와 무관한 noise(지역 축제)가 wiki 에 쌓임. 관심사
    category(AI/시장)와 관련 높은 뉴스만 채택하고 무관한 것은 relevance gate 에서
    제외하는 동작을 결정적으로 고정한다.
    """

    def _candidates(self):
        ai_topic = StudyTopicStub(
            "general/ai", "대형 AI 모델 출시", "ai-industry", kind=TopicKind.GENERAL_NEWS
        )
        rate_topic = StudyTopicStub(
            "general/rates", "미국 금리 급변", "markets", kind=TopicKind.GENERAL_NEWS
        )
        festival_topic = StudyTopicStub(
            "general/festival", "지역 축제", "community", kind=TopicKind.GENERAL_NEWS
        )
        topics = {
            ai_topic.topic_id: ai_topic,
            rate_topic.topic_id: rate_topic,
            festival_topic.topic_id: festival_topic,
        }
        results = [
            _result("general/ai", title="대형 AI 모델 출시"),
            _result("general/rates", title="미국 금리 급변"),
            _result("general/festival", title="지역 축제 개최"),
        ]
        return topics, results

    def test_relevant_ai_and_market_news_selected_festival_rejected(self):
        topics, results = self._candidates()
        scorer = InterestAwareScorer(
            interest_categories=frozenset({"ai-industry", "markets"})
        )

        selection = select_wiki_worthy(
            results, topics=topics, scorer=scorer, threshold=0.5
        )

        selected_titles = {r.title for r in selection.selected}
        rejected_titles = {r.title for r, _ in selection.rejected}
        assert selected_titles == {"대형 AI 모델 출시", "미국 금리 급변"}
        assert rejected_titles == {"지역 축제 개최"}

    def test_festival_rejection_records_reason(self):
        topics, results = self._candidates()
        scorer = InterestAwareScorer(
            interest_categories=frozenset({"ai-industry", "markets"})
        )

        selection = select_wiki_worthy(
            results, topics=topics, scorer=scorer, threshold=0.5
        )

        festival = next(
            (r, a) for r, a in selection.rejected if r.title == "지역 축제 개최"
        )
        _, assessment = festival
        assert assessment.reasons  # 왜 제외됐는지 감사 로그용 근거가 남는다.


# --------------------------------------------------------------------------- #
# 파이프라인 smoke — plan → registry fetch → select 가 end-to-end 로 흐른다.
# --------------------------------------------------------------------------- #


def test_full_source_pipeline_mixes_user_interest_and_general_news():
    """user_interest 통과 + general_news gate 적용이 한 번에 돌아가는 회귀.

    여러 Case 가 공유하는 실패 유형(파이프라인 단계 간 결합 깨짐)을 하나의 e2e
    경로로 고정한다.
    """
    interest = StudyTopicStub(
        "ai-industry/openai", "OpenAI 상장", "ai-industry", kind=TopicKind.USER_INTEREST
    )
    festival = StudyTopicStub(
        "general/festival", "지역 축제", "community", kind=TopicKind.GENERAL_NEWS
    )
    topics = {interest.topic_id: interest, festival.topic_id: festival}

    requests = plan_fetch_requests([interest, festival])

    # 등록된 collector 가 각 topic 에 결과를 1건씩 돌려준다.
    registry = CollectorRegistry()
    news = StubCollector(
        name="news-search-skill",
        results=[
            _result("ai-industry/openai", title="OpenAI 공시", confidence=0.4),
            _result("general/festival", title="지역 축제", confidence=0.4),
        ],
    )
    registry.register(news)

    results = registry.fetch_all(requests)
    selection = select_wiki_worthy(results, topics=topics, threshold=0.5)

    selected_topics = {r.request.topic_id for r in selection.selected}
    # user_interest 는 저신뢰여도 통과, general_news 는 저신뢰라 탈락.
    assert "ai-industry/openai" in selected_topics
    assert "general/festival" not in selected_topics
