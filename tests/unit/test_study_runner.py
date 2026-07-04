"""Daily study runner 검증 — registry → planner → updater → index store 흐름.

DoD:
- runner 가 topic registry → source planner → updater → index store 흐름을 실행한다.
- Markdown page 수동 섹션을 보존한다.
- daily note 가 생성된다.
- 모든 collector 는 테스트에서 fake 로 대체 가능하다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest
import yaml

from simpleclaw.study.collectors import (
    CollectorRegistry,
    StudyFetchRequest,
    StudyFetchResult,
)
from simpleclaw.study.runner import (
    StudyRunner,
    StudyRunSummary,
    load_daily_digest_prompt,
    load_topic_update_prompt,
)

FIXED_NOW = datetime(2026, 6, 29, 6, 0, 0, tzinfo=timezone.utc)


@dataclass
class FakeCollector:
    """topic_id → 수집 신뢰도 매핑으로 결과를 만드는 테스트용 collector.

    매핑에 없는 topic 은 빈 결과(no-op). 실제 도구 호출이 전혀 없으므로 네트워크
    없이 결정적으로 파이프라인을 검증한다.
    """

    name: str
    conf_by_topic: dict[str, float] = field(default_factory=dict)
    limitations: tuple[str, ...] = ()

    def fetch(self, request: StudyFetchRequest) -> Sequence[StudyFetchResult]:
        conf = self.conf_by_topic.get(request.topic_id)
        if conf is None:
            return ()
        return [
            StudyFetchResult(
                request=request,
                title=f"{request.topic_id} 최신 동향",
                text=f"{request.topic_id} 관련 본문 내용.",
                url=f"https://example.com/{request.topic_id}",
                source="example.com",
                confidence=conf,
                limitations=self.limitations,
            )
        ]


def _write_topics(wiki_dir, topics: list[dict]) -> None:
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "topics.yaml").write_text(
        yaml.safe_dump({"topics": topics}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _registry(*collectors) -> CollectorRegistry:
    reg = CollectorRegistry()
    for c in collectors:
        reg.register(c)
    return reg


def _make_runner(wiki_dir, registry, **kwargs) -> StudyRunner:
    return StudyRunner(
        wiki_dir=wiki_dir,
        collectors=registry,
        now=lambda: FIXED_NOW,
        run_id_factory=lambda _started: "run-test",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 핵심 흐름
# ---------------------------------------------------------------------------


def test_run_executes_full_pipeline(tmp_path):
    """registry → planner → collector → updater → index store 전 구간 실행."""
    wiki_dir = tmp_path / "wiki"
    _write_topics(
        wiki_dir,
        [
            {
                "id": "openai",
                "title": "OpenAI",
                "category": "ai-industry",
                "kind": "user_interest",
                "interest_score": 0.9,
            }
        ],
    )
    runner = _make_runner(
        wiki_dir, _registry(FakeCollector("news-search-skill", {"openai": 0.9}))
    )

    summary = runner.run()

    assert isinstance(summary, StudyRunSummary)
    assert summary.run_id == "run-test"
    assert summary.topics_considered == 1
    assert summary.topics_updated == 1
    assert summary.items_added == 1

    # wiki page 생성 + 출처 반영.
    page = (wiki_dir / "topics" / "openai" / "overview.md").read_text(encoding="utf-8")
    assert "# OpenAI" in page
    assert "https://example.com/openai" in page

    # index store 적재.
    conn = sqlite3.connect(wiki_dir / "index.sqlite")
    rows = conn.execute(
        "SELECT topic_id, confidence FROM study_items"
    ).fetchall()
    conn.close()
    assert rows == [("openai", 0.9)]

    # daily note 생성.
    note = (wiki_dir / "daily" / "2026-06-29.md").read_text(encoding="utf-8")
    assert "OpenAI" in note
    assert "run-test" in note

    # topics.yaml write-back.
    saved = yaml.safe_load((wiki_dir / "topics.yaml").read_text(encoding="utf-8"))
    topic = saved["topics"][0]
    assert topic["last_studied_at"] == FIXED_NOW.isoformat()
    assert topic["source_count"] == 1


def test_manual_section_preserved_across_run(tmp_path):
    """기존 페이지의 수동 섹션은 run 후에도 보존된다."""
    wiki_dir = tmp_path / "wiki"
    _write_topics(
        wiki_dir,
        [{"id": "openai", "title": "OpenAI", "category": "ai-industry"}],
    )
    page_path = wiki_dir / "topics" / "openai" / "overview.md"
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(
        "# OpenAI\n\n## 운영자 메모\n- 절대 지우지 말 것\n",
        encoding="utf-8",
    )

    runner = _make_runner(
        wiki_dir, _registry(FakeCollector("news-search-skill", {"openai": 0.9}))
    )
    runner.run()

    page = page_path.read_text(encoding="utf-8")
    assert "운영자 메모" in page
    assert "절대 지우지 말 것" in page
    assert "https://example.com/openai" in page  # 자동 갱신도 반영


def test_collectors_are_mockable_noop_when_empty(tmp_path):
    """collector 가 비어 있으면(placeholder 폴백) 안전한 no-op 으로 흐른다."""
    wiki_dir = tmp_path / "wiki"
    _write_topics(wiki_dir, [{"id": "t1", "title": "T1"}])

    runner = _make_runner(wiki_dir, CollectorRegistry())  # 등록된 collector 없음
    summary = runner.run()

    assert summary.topics_considered == 1
    assert summary.topics_updated == 0
    assert summary.items_added == 0
    assert any("collector" in lim for lim in summary.limitations)
    # 페이지/인덱스는 만들어지지 않는다.
    assert not (wiki_dir / "topics" / "t1" / "overview.md").exists()
    assert not (wiki_dir / "index.sqlite").exists()


def test_general_news_below_threshold_is_filtered(tmp_path):
    """일반 뉴스 후보는 relevance 임계 미만이면 wiki 에 쓰지 않는다."""
    wiki_dir = tmp_path / "wiki"
    _write_topics(
        wiki_dir,
        [
            {"id": "interest", "title": "관심사", "kind": "user_interest"},
            {"id": "rumor", "title": "루머", "kind": "general_news"},
        ],
    )
    runner = _make_runner(
        wiki_dir,
        _registry(
            FakeCollector(
                "news-search-skill",
                {"interest": 0.9, "rumor": 0.2},  # rumor 는 임계 미만
            )
        ),
    )
    summary = runner.run()

    assert summary.topics_considered == 2
    assert summary.topics_updated == 1  # interest 만 채택
    assert (wiki_dir / "topics" / "interest" / "overview.md").exists()
    assert not (wiki_dir / "topics" / "rumor" / "overview.md").exists()


def test_low_confidence_writes_open_questions(tmp_path):
    """저신뢰 user_interest 결과는 open_questions.md 에도 격리 기록된다."""
    wiki_dir = tmp_path / "wiki"
    _write_topics(
        wiki_dir,
        [{"id": "t", "title": "주제", "kind": "user_interest"}],
    )
    runner = _make_runner(
        wiki_dir,
        _registry(FakeCollector("news-search-skill", {"t": 0.3})),
    )
    runner.run()

    oq = (wiki_dir / "open_questions.md").read_text(encoding="utf-8")
    assert "Open Questions" in oq
    assert "[주제]" in oq
    page = (wiki_dir / "topics" / "t" / "overview.md").read_text(encoding="utf-8")
    assert "확인 필요" in page


def test_refresh_requested_topics_prioritized(tmp_path):
    """refresh 요청이 걸린 topic 이 우선 처리되고 플래그가 비워진다."""
    wiki_dir = tmp_path / "wiki"
    _write_topics(
        wiki_dir,
        [
            {"id": "a", "title": "A", "interest_score": 0.9},
            {
                "id": "b",
                "title": "B",
                "interest_score": 0.1,
                "refresh_requested_at": "2026-06-28T00:00:00+00:00",
            },
        ],
    )
    runner = _make_runner(
        wiki_dir,
        _registry(FakeCollector("news-search-skill", {"a": 0.9, "b": 0.9})),
        max_topics_per_run=1,  # 하나만 처리 → 우선순위 확인
    )
    summary = runner.run()

    assert summary.topics_considered == 1
    assert summary.topics_updated == 1
    # refresh 가 걸린 b 가 선택돼야 한다.
    assert (wiki_dir / "topics" / "b" / "overview.md").exists()
    assert not (wiki_dir / "topics" / "a" / "overview.md").exists()

    saved = yaml.safe_load((wiki_dir / "topics.yaml").read_text(encoding="utf-8"))
    b = next(t for t in saved["topics"] if t["id"] == "b")
    assert "refresh_requested_at" not in b  # 처리 후 비워짐


def test_archived_topics_excluded(tmp_path):
    """아카이브된 topic 은 공부 대상에서 제외된다."""
    wiki_dir = tmp_path / "wiki"
    _write_topics(
        wiki_dir,
        [
            {"id": "live", "title": "Live"},
            {"id": "dead", "title": "Dead", "status": "archived"},
        ],
    )
    runner = _make_runner(
        wiki_dir,
        _registry(FakeCollector("news-search-skill", {"live": 0.9, "dead": 0.9})),
    )
    summary = runner.run()

    assert summary.topics_considered == 1  # dead 제외
    assert (wiki_dir / "topics" / "live" / "overview.md").exists()
    assert not (wiki_dir / "topics" / "dead" / "overview.md").exists()


def test_empty_registry_file_is_safe(tmp_path):
    """topics.yaml 이 없으면 아무 일도 하지 않고 빈 요약을 반환한다."""
    wiki_dir = tmp_path / "wiki"
    runner = _make_runner(wiki_dir, CollectorRegistry())
    summary = runner.run()

    assert summary.topics_considered == 0
    assert summary.topics_updated == 0
    assert summary.items_added == 0


def test_index_compatible_with_study_status_store(tmp_path):
    """runner 가 쓴 index/topics 를 운영자 조회 store 가 그대로 읽을 수 있다."""
    from simpleclaw.agent.study_status import StudyWikiStore

    wiki_dir = tmp_path / "wiki"
    _write_topics(
        wiki_dir,
        [{"id": "t", "title": "주제", "kind": "user_interest"}],
    )
    runner = _make_runner(
        wiki_dir,
        _registry(FakeCollector("news-search-skill", {"t": 0.3})),
    )
    runner.run()

    store = StudyWikiStore(wiki_dir, now=lambda: FIXED_NOW)
    assert store.is_configured()
    report = store.status_report()
    assert report.total_topics == 1
    # 저신뢰(0.3) item 은 운영자 low-confidence 목록에 잡힌다.
    titles = {i.title for i in report.low_confidence_items}
    assert any(i.kind == "item" for i in report.low_confidence_items) or titles


# ---------------------------------------------------------------------------
# 프롬프트 로더 (YAML SoT)
# ---------------------------------------------------------------------------


def test_topic_update_prompt_loads_and_validates():
    spec = load_topic_update_prompt()
    assert spec.name == "topic_update"
    rendered = spec.format(
        topic_title="OpenAI",
        topic_category="ai-industry",
        interest_hint="AI 산업",
        existing_excerpt="(없음)",
        sources_block="- ...",
    )
    assert "OpenAI" in rendered


def test_daily_digest_prompt_loads_and_validates():
    spec = load_daily_digest_prompt()
    assert spec.name == "daily_digest"
    rendered = spec.format(
        date="2026-06-29",
        topics_considered=2,
        topics_updated=1,
        items_added=3,
        updated_topics_block="- OpenAI",
        limitations_block="- 없음",
    )
    assert "2026-06-29" in rendered


def test_missing_required_var_raises():
    spec = load_topic_update_prompt()
    with pytest.raises(Exception):
        spec.format(topic_title="x")  # 나머지 required_vars 누락
