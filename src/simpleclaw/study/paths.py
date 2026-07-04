"""Study Wiki 디렉터리 레이아웃 규약과 초기화.

위키는 단일 루트 디렉터리 아래에 다음 구조로 자료를 둔다.

    <root>/
      topics.yaml      — 추적 주제 레지스트리(StudyTopic 목록)
      topics/          — 주제별 본문 페이지(<topic_id>.md)
      daily/           — 일자별 스터디 로그(<YYYY-MM-DD>.md)

설계 결정 — 런타임 루트 분리:
    위키 데이터는 배포 repo(``~/.simpleclaw``)가 아니라 런타임 루트
    (``~/.simpleclaw-agent/default``) 아래 ``study/`` 에 둔다. 이는 daemon/agents
    등 다른 사용자 데이터와 같은 규약(부모 CLAUDE.md, config_sections/agents.py)
    을 따른다. 호출자는 다른 루트를 명시적으로 넘겨 테스트·멀티 인스턴스를
    분리할 수 있다.
"""

from __future__ import annotations

from pathlib import Path

# 위키 데이터의 기본 루트. config 의 다른 런타임 경로와 동일한 베이스를 쓴다.
DEFAULT_WIKI_ROOT = "~/.simpleclaw-agent/default/study"

# 레이아웃 상수 — 하드코딩 분산을 막기 위해 한 곳에 모은다.
TOPICS_FILE = "topics.yaml"
TOPICS_DIR = "topics"
DAILY_DIR = "daily"
# 구조화 retrieval index(SQLite) 파일명. 운영자 조회(StudyWikiStore, BIZ-395)와
# 동일한 위치를 가리키도록 한 곳에 고정한다.
INDEX_FILE = "index.sqlite"


def wiki_root(base: str | Path | None = None) -> Path:
    """위키 루트 경로를 ``~`` 가 풀린 절대 경로로 반환한다.

    Args:
        base: 사용할 루트. ``None`` 이면 :data:`DEFAULT_WIKI_ROOT` 를 쓴다.
            테스트나 멀티 인스턴스에서는 ``tmp_path`` 같은 격리 경로를 넘긴다.

    Returns:
        ``Path`` — 디렉터리를 생성하지는 않으며 경로 객체만 돌려준다.
    """
    raw = DEFAULT_WIKI_ROOT if base is None else base
    return Path(raw).expanduser()


def topics_yaml_path(base: str | Path | None = None) -> Path:
    """``topics.yaml`` 레지스트리 파일 경로를 반환한다."""
    return wiki_root(base) / TOPICS_FILE


def topics_dir(base: str | Path | None = None) -> Path:
    """주제 본문 페이지 디렉터리(``topics/``) 경로를 반환한다."""
    return wiki_root(base) / TOPICS_DIR


def daily_dir(base: str | Path | None = None) -> Path:
    """일자별 스터디 로그 디렉터리(``daily/``) 경로를 반환한다."""
    return wiki_root(base) / DAILY_DIR


def index_path(base: str | Path | None = None) -> Path:
    """구조화 retrieval index(``index.sqlite``) 파일 경로를 반환한다.

    :class:`~simpleclaw.study.index_store.StudyIndexStore` 와 운영자 조회
    ``StudyWikiStore`` 가 같은 파일을 보도록 위치를 일원화한다.
    """
    return wiki_root(base) / INDEX_FILE


def topic_page_path(topic_id: str, base: str | Path | None = None) -> Path:
    """주제 본문 페이지 파일(``topics/<topic_id>.md``) 경로를 반환한다.

    ``topic_id`` 는 안정적 식별자라 그대로 파일명에 쓴다. 경로 탈출(``..``,
    절대경로, 디렉터리 구분자)이 섞인 식별자는 위키 루트를 벗어나는 쓰기를
    유발할 수 있으므로 거부한다.

    Args:
        topic_id: ``StudyTopic.id``.
        base: 위키 루트(생략 시 기본 루트).

    Raises:
        ValueError: ``topic_id`` 가 비었거나 경로 구분자/상위 참조를 포함할 때.
    """
    if not topic_id or not topic_id.strip():
        raise ValueError("topic_id must be a non-empty string")
    # 경로 인젝션 방지: 식별자에 디렉터리 구분이나 상위 참조가 있으면 거부한다.
    if "/" in topic_id or "\\" in topic_id or topic_id in {".", ".."}:
        raise ValueError(f"invalid topic_id for a file name: {topic_id!r}")
    return topics_dir(base) / f"{topic_id}.md"


def init_wiki_root(base: str | Path | None = None) -> Path:
    """위키 루트와 필수 하위 구조(``topics.yaml``, ``topics/``, ``daily/``)를 만든다.

    멱등(idempotent)하게 동작한다 — 이미 있는 디렉터리/파일은 건드리지 않으며,
    기존 ``topics.yaml`` 내용도 덮어쓰지 않는다(사용자가 편집한 레지스트리 보존).

    Args:
        base: 위키 루트(생략 시 기본 루트).

    Returns:
        생성/확인된 위키 루트 ``Path``.
    """
    root = wiki_root(base)
    root.mkdir(parents=True, exist_ok=True)
    topics_dir(base).mkdir(parents=True, exist_ok=True)
    daily_dir(base).mkdir(parents=True, exist_ok=True)

    topics_file = topics_yaml_path(base)
    if not topics_file.exists():
        # 빈 레지스트리 시드. topic_registry.load_topics 가 빈 목록으로 해석한다.
        topics_file.write_text("topics: []\n", encoding="utf-8")

    return root
