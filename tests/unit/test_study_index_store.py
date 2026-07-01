"""StudyIndexStore 단위 테스트.

검증 항목:
1. initialize_schema 가 study_topics/study_items 를 idempotent 하게 생성한다.
2. upsert_topic 이 삽입/갱신을 모두 처리한다(PK 충돌 시 update).
3. add_item 이 새 행 id 를 돌려주고 컬럼을 round-trip 한다.
4. search_lexical 이 토큰 매칭 수 기준으로 정렬/필터한다.
5. fresh_items 가 freshness/신뢰도/status/valid_until gate 를 적용한다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from simpleclaw.study.index_store import StudyIndexStore
from simpleclaw.study.types import StudyItemStatus


@pytest.fixture
def store(tmp_path: Path) -> StudyIndexStore:
    s = StudyIndexStore(tmp_path / "study.sqlite")
    s.initialize_schema()
    return s


def test_study_index_store_upserts_and_searches(tmp_path: Path) -> None:
    # 이슈 DoD 예시 시나리오 그대로.
    store = StudyIndexStore(tmp_path / "study.sqlite")
    store.initialize_schema()
    store.upsert_topic(id="ai-industry-openai", label="OpenAI", interest_score=0.8)
    item_id = store.add_item(
        topic_id="ai-industry-openai",
        title="OpenAI IPO delay",
        text="OpenAI IPO delay should be treated as reported, not confirmed.",
        status=StudyItemStatus.REPORTED.value,
        confidence=0.7,
        retrieved_at="2026-06-29T06:30:00+09:00",
    )

    results = store.search_lexical("OpenAI IPO", limit=3)

    assert item_id > 0
    assert results[0].topic_id == "ai-industry-openai"


def test_initialize_schema_is_idempotent_and_creates_tables(
    tmp_path: Path,
) -> None:
    db = tmp_path / "study.sqlite"
    store = StudyIndexStore(db)
    store.initialize_schema()
    store.initialize_schema()  # 두 번 호출해도 에러 없이 no-op

    with sqlite3.connect(db) as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        indexes = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
    assert {"study_topics", "study_items"}.issubset(tables)
    assert "idx_study_items_topic" in indexes


def test_upsert_topic_inserts_then_updates(store: StudyIndexStore) -> None:
    store.upsert_topic(
        id="t1", label="Old", interest_score=0.3,
        created_at="2026-06-01T00:00:00+09:00",
    )
    store.upsert_topic(
        id="t1", label="New", interest_score=0.9,
        created_at="2026-06-29T00:00:00+09:00",
    )

    conn = store._connect()
    try:
        row = conn.execute(
            "SELECT label, interest_score, created_at FROM study_topics WHERE id='t1'"
        ).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM study_topics").fetchone()[0]
    finally:
        conn.close()

    assert count == 1  # 충돌 시 새 행을 만들지 않는다
    assert row["label"] == "New"
    assert row["interest_score"] == pytest.approx(0.9)
    # created_at 은 최초 삽입 값을 보존한다(충돌 시 갱신하지 않음)
    assert row["created_at"] == "2026-06-01T00:00:00+09:00"


def test_add_item_round_trips_columns(store: StudyIndexStore) -> None:
    store.upsert_topic(id="t1", label="Topic")
    item_id = store.add_item(
        topic_id="t1",
        title="Title",
        text="Body text",
        retrieved_at="2026-06-29T06:30:00+09:00",
        source_url="https://example.com/a",
        source_title="Example",
        status=StudyItemStatus.CONFIRMED.value,
        confidence=0.9,
        importance=0.4,
        published_at="2026-06-28T00:00:00+09:00",
        valid_until="2026-07-29T00:00:00+09:00",
        metadata={"k": "v"},
    )

    rec = store.get_item(item_id)
    assert rec is not None
    assert rec.topic_id == "t1"
    assert rec.source_url == "https://example.com/a"
    assert rec.status == "confirmed"
    assert rec.confidence == pytest.approx(0.9)
    assert rec.valid_until == "2026-07-29T00:00:00+09:00"
    assert rec.metadata == {"k": "v"}


def test_search_lexical_ranks_by_term_match_count(store: StudyIndexStore) -> None:
    store.upsert_topic(id="t1", label="Topic")
    # 두 토큰 모두 매칭
    both = store.add_item(
        topic_id="t1",
        title="OpenAI IPO timeline",
        text="discussion",
        retrieved_at="2026-06-29T06:00:00+09:00",
    )
    # 한 토큰만 매칭
    store.add_item(
        topic_id="t1",
        title="OpenAI hiring",
        text="unrelated",
        retrieved_at="2026-06-29T07:00:00+09:00",
    )

    results = store.search_lexical("OpenAI IPO")
    assert len(results) == 2
    assert results[0].id == both  # 토큰 2개 매칭이 먼저


def test_search_lexical_scopes_to_topic(store: StudyIndexStore) -> None:
    store.upsert_topic(id="t1", label="T1")
    store.upsert_topic(id="t2", label="T2")
    store.add_item(
        topic_id="t1", title="OpenAI news", text="x",
        retrieved_at="2026-06-29T06:00:00+09:00",
    )
    keep = store.add_item(
        topic_id="t2", title="OpenAI news", text="y",
        retrieved_at="2026-06-29T06:00:00+09:00",
    )

    results = store.search_lexical("OpenAI", topic_id="t2")
    assert [r.id for r in results] == [keep]


def test_search_lexical_empty_query_returns_empty(store: StudyIndexStore) -> None:
    store.upsert_topic(id="t1", label="Topic")
    store.add_item(
        topic_id="t1", title="x", text="y",
        retrieved_at="2026-06-29T06:00:00+09:00",
    )
    assert store.search_lexical("   ") == []


def test_search_lexical_escapes_wildcards(store: StudyIndexStore) -> None:
    store.upsert_topic(id="t1", label="Topic")
    store.add_item(
        topic_id="t1",
        title="plain title",
        text="no percent here",
        retrieved_at="2026-06-29T06:00:00+09:00",
    )
    # '%' 는 리터럴로 취급되어 매칭이 없어야 한다(와일드카드로 해석되면 전부 매칭됨)
    assert store.search_lexical("%") == []


def test_fresh_items_applies_freshness_and_status_gate(
    store: StudyIndexStore,
) -> None:
    store.upsert_topic(id="t1", label="Topic")
    now = "2026-06-29T12:00:00+09:00"
    # 신선 + confirmed → 포함
    fresh_id = store.add_item(
        topic_id="t1",
        title="fresh",
        text="recent",
        status=StudyItemStatus.CONFIRMED.value,
        confidence=0.8,
        retrieved_at="2026-06-29T06:00:00+09:00",
    )
    # 창 밖(30시간 전) → 제외
    store.add_item(
        topic_id="t1",
        title="stale window",
        text="old",
        status=StudyItemStatus.CONFIRMED.value,
        confidence=0.8,
        retrieved_at="2026-06-28T06:00:00+09:00",
    )
    # 신선하지만 STALE status → 제외
    store.add_item(
        topic_id="t1",
        title="marked stale",
        text="expired",
        status=StudyItemStatus.STALE.value,
        confidence=0.8,
        retrieved_at="2026-06-29T11:00:00+09:00",
    )
    # 신선하지만 confidence 미달 → 제외
    store.add_item(
        topic_id="t1",
        title="lowconf",
        text="weak",
        status=StudyItemStatus.CONFIRMED.value,
        confidence=0.2,
        retrieved_at="2026-06-29T11:00:00+09:00",
    )

    results = store.fresh_items(within_hours=24, now=now, min_confidence=0.5)
    assert [r.id for r in results] == [fresh_id]


def test_fresh_items_respects_valid_until(store: StudyIndexStore) -> None:
    store.upsert_topic(id="t1", label="Topic")
    now = "2026-06-29T12:00:00+09:00"
    # 신선하지만 valid_until 이 now 이전 → 만료로 제외
    store.add_item(
        topic_id="t1",
        title="expired",
        text="body",
        confidence=0.9,
        retrieved_at="2026-06-29T06:00:00+09:00",
        valid_until="2026-06-29T10:00:00+09:00",
    )
    # valid_until 이 미래 → 포함
    keep = store.add_item(
        topic_id="t1",
        title="valid",
        text="body",
        confidence=0.9,
        retrieved_at="2026-06-29T06:00:00+09:00",
        valid_until="2026-07-01T00:00:00+09:00",
    )

    results = store.fresh_items(within_hours=24, now=now)
    assert [r.id for r in results] == [keep]
