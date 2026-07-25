"""Daily run 통합 검증 — 최근 관심 신호 → active topic → daily note (BIZ-434).

temp wiki 에서 전체 경로를 고정한다:
recent interest signal → evolution 이 candidate 생성/active 승격 →
daily run 이 그 topic 을 공부 → topics.yaml/topic page/daily note 반영.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml

from simpleclaw.study.collectors import (
    CollectorRegistry,
    StudyFetchRequest,
    StudyFetchResult,
)
from simpleclaw.study.interest_signals import InterestSignal
from simpleclaw.study.runner import StudyRunner
from simpleclaw.study.signal_provider import StaticStudySignalProvider

FIXED_NOW = datetime(2026, 7, 12, tzinfo=UTC)


class EchoCollector:
    """요청 쿼리를 그대로 결과로 돌려주는 테스트 collector."""

    name = "news-search-skill"

    def fetch(self, request: StudyFetchRequest):
        return [
            StudyFetchResult(
                request=request,
                title=f"Result for {request.query}",
                text="source text",
                url="https://example.com/source",
                source="test",
                confidence=0.9,
            )
        ]


def _make_runner(wiki_dir: Path, provider) -> StudyRunner:
    registry = CollectorRegistry()
    registry.register(EchoCollector())
    return StudyRunner(
        wiki_dir=wiki_dir,
        collectors=registry,
        signal_provider=provider,
        now=lambda: FIXED_NOW,
        run_id_factory=lambda _started: "run-evo-test",
        max_topics_per_run=4,
    )


def test_runner_creates_active_topic_from_recent_interest_signal(tmp_path: Path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "topics.yaml").write_text("topics: []\n", encoding="utf-8")

    provider = StaticStudySignalProvider(
        [
            InterestSignal(
                topic_hint="AI coding agents",
                text="AI coding agents 공부해줘",
                source="user_message",
                weight=0.92,
                confidence=0.8,
                source_ref="msg-1",
            )
        ]
    )
    runner = _make_runner(wiki_dir, provider)

    summary = runner.run()

    # topics.yaml: 신호에서 만들어진 topic 이 active 로 저장된다.
    data = yaml.safe_load((wiki_dir / "topics.yaml").read_text(encoding="utf-8"))
    topics = data["topics"]
    assert topics[0]["id"] == "ai-coding-agents"
    assert topics[0]["status"] == "active"
    assert topics[0]["source"] == "interest"
    assert topics[0]["source_signals"][0]["source"] == "user_message"

    # run summary: evolution 결과가 반영되고 topic 이 실제로 공부됐다.
    assert summary.evolution is not None
    assert summary.evolution.created == 1
    assert "ai-coding-agents" in summary.evolution.promoted_ids
    assert summary.topics_updated == 1

    # topic page + daily note 생성.
    assert (wiki_dir / "topics" / "ai-coding-agents" / "overview.md").is_file()
    daily = (wiki_dir / "daily" / "2026-07-12.md").read_text(encoding="utf-8")
    assert "ai-coding-agents" in daily
    assert "## Topic Evolution" in daily
    assert "created: 1" in daily
    assert "## Topic Selection" in daily
    assert "recent interest signal" in daily


def test_auto_report_only_signal_creates_candidate_but_is_not_studied(
    tmp_path: Path,
):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "topics.yaml").write_text("topics: []\n", encoding="utf-8")

    provider = StaticStudySignalProvider(
        [
            InterestSignal(
                topic_hint="Random automated news",
                text="자동 브리핑에서 스친 뉴스",
                source="auto_report",
                weight=0.25,
                confidence=0.1,
            )
        ]
    )
    runner = _make_runner(wiki_dir, provider)

    summary = runner.run()

    data = yaml.safe_load((wiki_dir / "topics.yaml").read_text(encoding="utf-8"))
    assert data["topics"][0]["id"] == "random-automated-news"
    assert data["topics"][0]["status"] == "candidate"
    # candidate 는 공부 대상이 아니다.
    assert summary.topics_considered == 0
    assert not (wiki_dir / "topics" / "random-automated-news").exists()
    # 하지만 daily note 에는 evolution 기록이 남는다(감사 가능).
    daily = (wiki_dir / "daily" / "2026-07-12.md").read_text(encoding="utf-8")
    assert "## Topic Evolution" in daily
    assert "created: 1" in daily


def test_pinned_seed_survives_evolution_and_uses_split_queries(tmp_path: Path):
    """pinned seed 는 sticky 유지 + search_queries 분리로 label 을 쿼리로 안 쓴다."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "topics.yaml").write_text(
        yaml.safe_dump(
            {
                "topics": [
                    {
                        "id": "market-reports-us-kr",
                        "title": "US/KR 시장 리포트",
                        "label": "US/Korean market reports and watchpoints",
                        "status": "pinned",
                        "source": "operator-bootstrap",
                        "category": "general",
                        "source_count": 8,
                        "search_queries": [
                            "US stock market latest news",
                            "KOSPI Korean stock market latest news",
                        ],
                    }
                ]
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    seen_queries: list[str] = []

    class RecordingCollector(EchoCollector):
        def fetch(self, request: StudyFetchRequest):
            seen_queries.append(request.query)
            return super().fetch(request)

    registry = CollectorRegistry()
    registry.register(RecordingCollector())
    runner = StudyRunner(
        wiki_dir=wiki_dir,
        collectors=registry,
        signal_provider=StaticStudySignalProvider(()),
        now=lambda: FIXED_NOW,
        run_id_factory=lambda _started: "run-evo-test",
    )

    runner.run()

    # label 이 아니라 split 검색 쿼리가 fetch 에 쓰였다.
    assert "US/Korean market reports and watchpoints" not in seen_queries
    assert "US stock market latest news" in seen_queries
    assert "KOSPI Korean stock market latest news" in seen_queries

    saved = yaml.safe_load((wiki_dir / "topics.yaml").read_text(encoding="utf-8"))
    topic = saved["topics"][0]
    assert topic["status"] == "pinned"  # sticky
    assert topic["source_count"] >= 8  # 운영 키 보존(+이번 run 수집분)
    assert topic["search_queries"] == [
        "US stock market latest news",
        "KOSPI Korean stock market latest news",
    ]

    daily = (wiki_dir / "daily" / "2026-07-12.md").read_text(encoding="utf-8")
    assert "`market-reports-us-kr` — pinned seed (operator-bootstrap)" in daily
