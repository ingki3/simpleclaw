"""Agent Study Wiki 패키지.

SimpleClaw 가 사용자의 관심사·Dreaming 결과·중요 뉴스를 매일 "공부"해서
Markdown 위키로 축적하고, 질문이 들어올 때 맥락으로 끌어다 쓰기 위한 저장소다.

설계 결정 — Markdown 이 source of truth:
    초기 MVP 의 사람이 보는 source of truth 는 Markdown page 다. DB/임베딩은
    어디까지나 검색 인덱스 용도이며, 운영자(형님)가 직접 열어 읽고 수정할 수
    있어야 한다. 따라서 본 패키지는 다음 단순 계층만 제공한다.

    - :mod:`~simpleclaw.study.types` — 핵심 dataclass(주제/페이지/출처).
    - :mod:`~simpleclaw.study.paths` — 위키 루트(``topics.yaml``/``daily/``/
      ``topics/``) 경로 규약과 초기화.
    - :mod:`~simpleclaw.study.markdown` — ``StudyPage`` ↔ Markdown 직렬화.
    - :mod:`~simpleclaw.study.topic_registry` — ``topics.yaml`` ↔ ``StudyTopic``.

    DB·임베딩·일일 스터디 파이프라인 같은 상위 기능은 후속 이슈에서 이 계층을
    얹는다.
"""

from __future__ import annotations

from .markdown import parse_study_page, render_study_page
from .paths import (
    daily_dir,
    init_wiki_root,
    topic_page_path,
    topics_dir,
    topics_yaml_path,
    wiki_root,
)
from .topic_registry import TopicRegistry, load_topics, save_topics
from .types import (
    StudyItemStatus,
    StudyPage,
    StudySource,
    StudyTopic,
)

__all__ = [
    # types
    "StudyItemStatus",
    "StudySource",
    "StudyTopic",
    "StudyPage",
    # paths
    "wiki_root",
    "topics_yaml_path",
    "daily_dir",
    "topics_dir",
    "topic_page_path",
    "init_wiki_root",
    # markdown
    "render_study_page",
    "parse_study_page",
    # topic registry
    "TopicRegistry",
    "load_topics",
    "save_topics",
]
