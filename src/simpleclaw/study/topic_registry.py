"""``topics.yaml`` ↔ ``StudyTopic`` 레지스트리.

위키가 추적하는 주제 목록을 사람이 읽고 고칠 수 있는 단일 YAML 파일로
관리한다. 파일 포맷은 다음과 같다.

    topics:
      - id: ai-industry-openai
        label: OpenAI
        priority: high
        tags: [ai, industry]
        interest_score: 0.8
        ...

설계 결정 — 관대한 로드, 정규화된 저장:
    ``load_topics`` 는 사람이 손으로 편집하다 생긴 결손/잉여 키에 관대해야
    한다(알 수 없는 키는 무시, 누락 키는 dataclass 기본값). 반대로
    ``save_topics`` 는 항상 정규 스키마로 다시 써서 파일을 안정화한다.
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import yaml

from .paths import topics_yaml_path
from .types import StudyTopic

# StudyTopic 의 유효 필드 이름 집합 — 알 수 없는 YAML 키를 걸러내는 데 쓴다.
_TOPIC_FIELDS = {f.name for f in fields(StudyTopic)}


def _topic_from_dict(data: dict) -> StudyTopic | None:
    """YAML dict 한 건을 ``StudyTopic`` 으로 변환한다(잘못된 항목은 ``None``).

    ``id`` 가 없으면 식별 불가 항목이므로 건너뛴다. 알 수 없는 키는 버리고,
    누락 키는 dataclass 기본값에 맡긴다.
    """
    if not isinstance(data, dict):
        return None
    topic_id = data.get("id")
    if not isinstance(topic_id, str) or not topic_id.strip():
        return None
    kwargs = {k: v for k, v in data.items() if k in _TOPIC_FIELDS}
    # label 이 없으면 식별자라도 보여주도록 id 로 채운다.
    kwargs.setdefault("label", topic_id)
    return StudyTopic(**kwargs)


def _topic_to_dict(topic: StudyTopic) -> dict:
    """``StudyTopic`` 을 YAML 직렬화용 dict 로 변환한다(필드 정의 순서 보존)."""
    return {f.name: getattr(topic, f.name) for f in fields(StudyTopic)}


def load_topics(path: str | Path | None = None) -> list[StudyTopic]:
    """``topics.yaml`` 에서 주제 목록을 로드한다.

    파일이 없거나 비었거나 형식이 깨졌으면 빈 목록을 반환한다(봇은 주제 0개로도
    동작해야 한다).

    Args:
        path: ``topics.yaml`` 경로. ``None`` 이면 기본 위키 루트의 파일을 쓴다.

    Returns:
        ``StudyTopic`` 목록.
    """
    topics_path = topics_yaml_path() if path is None else Path(path)
    if not topics_path.is_file():
        return []
    try:
        data = yaml.safe_load(topics_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("topics", [])
    if not isinstance(raw, list):
        return []
    topics: list[StudyTopic] = []
    for item in raw:
        topic = _topic_from_dict(item)
        if topic is not None:
            topics.append(topic)
    return topics


def save_topics(topics: list[StudyTopic], path: str | Path | None = None) -> None:
    """주제 목록을 ``topics.yaml`` 로 정규 스키마로 저장한다(원자적 쓰기).

    부모 디렉터리가 없으면 만든다. 임시 파일에 먼저 쓰고 ``replace`` 로 교체해
    중간에 끊겨도 기존 파일이 깨지지 않게 한다.

    Args:
        topics: 저장할 주제 목록.
        path: ``topics.yaml`` 경로. ``None`` 이면 기본 위키 루트의 파일을 쓴다.
    """
    topics_path = topics_yaml_path() if path is None else Path(path)
    topics_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"topics": [_topic_to_dict(t) for t in topics]}
    text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    tmp = topics_path.with_suffix(topics_path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(topics_path)


class TopicRegistry:
    """``topics.yaml`` 을 메모리에서 다루는 얇은 레지스트리.

    주제의 조회/추가/갱신을 ``id`` 기준으로 편하게 하기 위한 헬퍼이며, 자동
    저장은 하지 않는다(:meth:`save` 를 명시 호출). 디스크가 source of truth 인
    설계상, 호출자가 변경 시점을 통제하도록 한다.
    """

    def __init__(self, topics: list[StudyTopic] | None = None, path: str | Path | None = None):
        """레지스트리를 초기화한다.

        Args:
            topics: 초기 주제 목록(생략 시 빈 목록).
            path: 저장/로드 대상 ``topics.yaml`` 경로(생략 시 기본 위키 루트).
        """
        self._path = topics_yaml_path() if path is None else Path(path)
        # id 순서를 보존하기 위해 dict(삽입 순서 보장) 로 보관한다.
        self._topics: dict[str, StudyTopic] = {}
        for topic in topics or []:
            self._topics[topic.id] = topic

    @classmethod
    def load(cls, path: str | Path | None = None) -> "TopicRegistry":
        """디스크의 ``topics.yaml`` 에서 레지스트리를 로드한다."""
        resolved = topics_yaml_path() if path is None else Path(path)
        return cls(load_topics(resolved), path=resolved)

    @property
    def path(self) -> Path:
        """이 레지스트리가 읽고 쓰는 ``topics.yaml`` 경로."""
        return self._path

    def list(self) -> list[StudyTopic]:
        """등록된 주제를 삽입 순서대로 반환한다."""
        return list(self._topics.values())

    def get(self, topic_id: str) -> StudyTopic | None:
        """``id`` 로 주제를 조회한다(없으면 ``None``)."""
        return self._topics.get(topic_id)

    def __contains__(self, topic_id: object) -> bool:
        return isinstance(topic_id, str) and topic_id in self._topics

    def __len__(self) -> int:
        return len(self._topics)

    def upsert(self, topic: StudyTopic) -> None:
        """주제를 추가하거나 같은 ``id`` 가 있으면 교체한다."""
        self._topics[topic.id] = topic

    def remove(self, topic_id: str) -> bool:
        """``id`` 로 주제를 제거한다(있었으면 ``True``)."""
        return self._topics.pop(topic_id, None) is not None

    def save(self, path: str | Path | None = None) -> None:
        """현재 주제 목록을 디스크에 저장한다."""
        save_topics(self.list(), path=path or self._path)
