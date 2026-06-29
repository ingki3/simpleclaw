"""Study Wiki 주제 레지스트리·경로 초기화 단위 테스트.

``topics.yaml`` 왕복, 위키 루트 초기화(멱등성), 손편집 관대성을 검증한다.
"""

from __future__ import annotations

from pathlib import Path

from simpleclaw.study.paths import (
    daily_dir,
    init_wiki_root,
    topic_page_path,
    topics_dir,
    topics_yaml_path,
)
from simpleclaw.study.topic_registry import (
    TopicRegistry,
    load_topics,
    save_topics,
)
from simpleclaw.study.types import StudyTopic


def test_init_wiki_root_creates_structure(tmp_path: Path):
    root = init_wiki_root(tmp_path / "study")
    assert root.is_dir()
    assert topics_dir(tmp_path / "study").is_dir()
    assert daily_dir(tmp_path / "study").is_dir()
    topics_file = topics_yaml_path(tmp_path / "study")
    assert topics_file.is_file()
    # 시드된 빈 레지스트리는 빈 목록으로 로드되어야 한다.
    assert load_topics(topics_file) == []


def test_init_wiki_root_is_idempotent_and_preserves_topics(tmp_path: Path):
    base = tmp_path / "study"
    init_wiki_root(base)
    save_topics([StudyTopic(id="t1", label="T1")], topics_yaml_path(base))
    # 다시 초기화해도 기존 topics.yaml 을 덮어쓰지 않는다.
    init_wiki_root(base)
    loaded = load_topics(topics_yaml_path(base))
    assert [t.id for t in loaded] == ["t1"]


def test_topic_page_path_rejects_path_injection(tmp_path: Path):
    base = tmp_path / "study"
    assert topic_page_path("ai-openai", base).name == "ai-openai.md"
    for bad in ["../escape", "a/b", "", ".."]:
        try:
            topic_page_path(bad, base)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad!r}")


def test_save_and_load_topics_round_trip(tmp_path: Path):
    path = tmp_path / "topics.yaml"
    topics = [
        StudyTopic(
            id="ai-industry-openai",
            label="OpenAI",
            description="생성형 AI 대표 기업",
            priority="high",
            tags=["ai", "industry"],
            interest_score=0.8,
            importance_score=0.6,
            metadata={"watch": True},
        ),
        StudyTopic(id="kr-economy", label="한국 경제"),
    ]
    save_topics(topics, path)
    loaded = load_topics(path)

    assert [t.id for t in loaded] == ["ai-industry-openai", "kr-economy"]
    first = loaded[0]
    assert first.priority == "high"
    assert first.tags == ["ai", "industry"]
    assert first.interest_score == 0.8
    assert first.metadata == {"watch": True}


def test_load_topics_missing_file_returns_empty(tmp_path: Path):
    assert load_topics(tmp_path / "nope.yaml") == []


def test_load_topics_tolerates_unknown_keys_and_missing_id(tmp_path: Path):
    path = tmp_path / "topics.yaml"
    path.write_text(
        "topics:\n"
        "  - id: valid\n"
        "    label: Valid\n"
        "    unexpected_key: 123\n"
        "  - label: 'id 없는 항목'\n",  # id 누락 → 건너뜀
        encoding="utf-8",
    )
    loaded = load_topics(path)
    assert [t.id for t in loaded] == ["valid"]
    assert loaded[0].label == "Valid"


def test_topic_registry_crud(tmp_path: Path):
    path = tmp_path / "topics.yaml"
    reg = TopicRegistry(path=path)
    assert len(reg) == 0

    reg.upsert(StudyTopic(id="t1", label="T1"))
    reg.upsert(StudyTopic(id="t2", label="T2"))
    assert "t1" in reg
    assert len(reg) == 2

    # 같은 id 로 upsert 하면 교체.
    reg.upsert(StudyTopic(id="t1", label="T1-updated"))
    assert reg.get("t1").label == "T1-updated"
    assert len(reg) == 2

    reg.save()
    reloaded = TopicRegistry.load(path)
    assert [t.id for t in reloaded.list()] == ["t1", "t2"]

    assert reg.remove("t1") is True
    assert reg.remove("missing") is False
    assert "t1" not in reg
