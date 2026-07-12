"""Study Wiki 핵심 dataclass 단위 테스트.

타입 자체에는 로직이 거의 없으므로, 기본값·불변성·열거형 값이 후속 직렬화
계층의 가정과 일치하는지(특히 ``str`` enum 의 값 문자열)를 고정한다.
"""

from __future__ import annotations

from pathlib import Path

from simpleclaw.study.types import (
    StudyItemStatus,
    StudyPage,
    StudySource,
    StudyTopic,
)


def test_study_item_status_values_are_plain_strings():
    # str enum 이므로 직렬화 시 값 문자열이 그대로 떨어져야 한다.
    assert StudyItemStatus.CONFIRMED == "confirmed"
    assert StudyItemStatus.REPORTED.value == "reported"
    assert {s.value for s in StudyItemStatus} == {
        "confirmed",
        "reported",
        "rumored",
        "analysis",
        "stale",
        "unknown",
    }


def test_study_source_is_frozen_with_defaults():
    source = StudySource(title="매일경제", url="https://mk.co.kr/x")
    assert source.source_type == "web"
    assert source.published_at is None
    assert source.confidence == 0.0
    # frozen dataclass 는 속성 변경이 막혀야 한다.
    try:
        source.title = "changed"  # type: ignore[misc]
    except Exception as exc:  # FrozenInstanceError
        assert "FrozenInstance" in type(exc).__name__
    else:
        raise AssertionError("StudySource should be immutable")


def test_study_topic_defaults_are_independent():
    a = StudyTopic(id="t1", label="T1")
    b = StudyTopic(id="t2", label="T2")
    a.tags.append("ai")
    a.metadata["k"] = "v"
    # default_factory 덕분에 인스턴스 간 가변 기본값이 공유되면 안 된다.
    assert b.tags == []
    assert b.metadata == {}
    assert a.priority == "medium"
    assert a.status == "active"
    assert a.source == "manual"


def test_study_topic_supports_evolution_metadata_defaults():
    topic = StudyTopic(id="new-interest", label="New Interest")

    assert topic.category == "general"
    assert topic.kind == "user_interest"
    assert topic.status == "active"
    assert topic.search_queries == []
    assert topic.source_signals == []
    assert topic.mention_count == 0
    assert topic.peak_score == 0.0
    assert topic.last_signal_at is None
    assert topic.last_studied_at is None


def test_study_topic_mutable_defaults_are_independent_for_new_fields():
    a = StudyTopic(id="a", label="A")
    b = StudyTopic(id="b", label="B")

    a.search_queries.append("query")
    a.source_signals.append({"source": "user_message"})
    a.metadata["x"] = 1

    assert b.search_queries == []
    assert b.source_signals == []
    assert b.metadata == {}


def test_study_page_defaults():
    page = StudyPage(topic_id="t1", path=Path("/tmp/t1.md"), title="T1")
    assert page.summary == ""
    assert page.current_state == []
    assert page.historical_context == []
    assert page.personal_relevance == []
    assert page.answer_guidance == []
    assert page.open_questions == []
    assert page.sources == []
    assert page.updated_at is None
