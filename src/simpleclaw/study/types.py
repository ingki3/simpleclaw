"""Study Wiki 핵심 자료 구조 정의.

이 모듈은 위키 전반에서 공유하는 dataclass 와 열거형만 담는다. I/O·직렬화
로직은 :mod:`~simpleclaw.study.markdown` / :mod:`~simpleclaw.study.topic_registry`
가 책임지고, 여기서는 순수 데이터 형태에만 집중한다.

설계 결정 — 상태(status)를 문자열 enum 으로:
    뉴스/소문/분석을 한 페이지에 섞으면 "보도 단계"를 "확정"처럼 다루는 사고가
    난다(부모 로드맵 §배경 참조). 그래서 사실의 신뢰 수준을 ``StudyItemStatus``
    로 명시해 confirmed/reported/rumored/analysis 를 구분할 수 있게 한다.
    ``str`` 을 함께 상속시켜 YAML/JSON 직렬화 시 값이 그대로 문자열로 떨어지게
    했다(사람이 읽는 Markdown/YAML 친화).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class StudyItemStatus(str, Enum):
    """스터디 항목 한 줄의 사실 신뢰 수준.

    뉴스 보도와 확정 사실, 소문, 분석을 같은 무게로 섞지 않기 위한 분류축이다.
    ``str`` 을 상속하므로 YAML/JSON 으로 직렬화하면 값 문자열이 그대로 남는다.
    """

    CONFIRMED = "confirmed"  # 1차 출처 또는 공식 발표로 확정된 사실
    REPORTED = "reported"  # 언론 보도 단계 — 확정은 아님
    RUMORED = "rumored"  # 소문/유출/미확인
    ANALYSIS = "analysis"  # 해석·전망·의견
    STALE = "stale"  # 시간이 지나 신선도가 떨어진 항목
    UNKNOWN = "unknown"  # 분류 불가/미지정 (안전한 기본값)


@dataclass(frozen=True)
class StudySource:
    """스터디 페이지가 인용하는 출처 한 건.

    immutable(frozen) 로 둔 이유: 한 번 수집한 출처의 메타데이터는 이후 변하지
    않아야 추적·중복제거가 안정적이기 때문이다.

    Attributes:
        title: 출처 제목(매체명/기사명 등 사람이 식별할 표기).
        url: 원문 URL.
        source_type: 출처 유형(``"web"``, ``"rss"``, ``"dreaming"`` 등).
        published_at: 원문 발행 시각(ISO8601 문자열) 또는 미상이면 ``None``.
        retrieved_at: 에이전트가 수집한 시각(ISO8601 문자열).
        confidence: 0.0~1.0 신뢰도 점수.
    """

    title: str
    url: str
    source_type: str = "web"
    published_at: str | None = None
    retrieved_at: str | None = None
    confidence: float = 0.0


@dataclass
class StudyTopic:
    """위키가 추적하는 학습 주제 한 건(``topics.yaml`` 항목 ↔ ``topics/<id>.md``).

    주제는 페이지의 "목차/메타" 역할이다. 본문(현재 상태·맥락 등)은 별도
    ``StudyPage`` Markdown 에 담고, 여기서는 우선순위·관심/중요 점수처럼 큐잉과
    스케줄링에 쓰이는 속성만 보관한다.

    Attributes:
        id: 안정적 식별자(파일명·인덱스 키). 예: ``ai-industry-openai``.
        label: 사람이 읽는 표시 이름.
        description: 주제 한 줄 설명.
        priority: 학습 우선순위(``"low"``/``"medium"``/``"high"``).
        status: 주제 활성 상태(``"active"``/``"paused"``/``"archived"``).
        tags: 분류 태그.
        source: 주제가 생긴 출처(``"manual"``/``"interest"``/``"dreaming"`` 등).
        interest_score: 사용자 관심 신호로 추정한 점수.
        importance_score: 뉴스/사회적 중요도로 추정한 점수.
        created_at: 생성 시각(ISO8601) 또는 ``None``.
        updated_at: 최종 갱신 시각(ISO8601) 또는 ``None``.
        metadata: 확장용 자유 형식 메타데이터.
    """

    id: str
    label: str
    description: str = ""
    priority: str = "medium"
    status: str = "active"
    tags: list[str] = field(default_factory=list)
    source: str = "manual"
    interest_score: float = 0.0
    importance_score: float = 0.0
    created_at: str | None = None
    updated_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StudyPage:
    """주제 하나에 대응하는 Markdown 위키 페이지의 구조화 표현.

    이 dataclass 는 Markdown 본문과 1:1 대응하며,
    :func:`~simpleclaw.study.markdown.render_study_page` /
    :func:`~simpleclaw.study.markdown.parse_study_page` 로 왕복 직렬화된다.
    각 섹션을 ``list[str]`` 로 둔 이유: Markdown 의 불릿 목록과 자연스럽게
    매핑되고, 사람이 한 줄씩 추가/삭제하기 쉬운 편집 단위이기 때문이다.

    Attributes:
        topic_id: 대응하는 ``StudyTopic.id``.
        path: 페이지 Markdown 파일 경로.
        title: 페이지 제목(H1).
        summary: 페이지 도입 요약(H1 바로 아래 문단).
        current_state: "현재 상태" 불릿 목록.
        historical_context: "역사적/사회적 맥락" 불릿 목록.
        personal_relevance: "형님 관심사와의 연결" 불릿 목록.
        answer_guidance: "답변 시 주의사항" 불릿 목록.
        open_questions: "열린 질문" 불릿 목록.
        sources: 인용 출처 목록.
        updated_at: 최종 갱신 시각(ISO8601) 또는 ``None``.
    """

    topic_id: str
    path: Path
    title: str
    summary: str = ""
    current_state: list[str] = field(default_factory=list)
    historical_context: list[str] = field(default_factory=list)
    personal_relevance: list[str] = field(default_factory=list)
    answer_guidance: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    sources: list[StudySource] = field(default_factory=list)
    updated_at: str | None = None


@dataclass(frozen=True)
class StudyItemRecord:
    """SQLite retrieval index(``study_items`` 한 행)의 메모리 표현.

    Markdown ``StudyPage`` 가 사람이 읽는 source of truth 라면, 이 record 는 질문 시
    빠른 retrieval/freshness filtering 을 위한 *구조화 index* 의 read model 이다
    (:mod:`~simpleclaw.study.index_store`). 컬럼과 1:1 대응하며, ``metadata`` 는
    ``metadata_json`` 을 역직렬화한 dict, ``embedding`` 은 BLOB raw bytes(없으면
    ``None``)다. 신선도/신뢰도/출처 메타데이터를 보존해 응답 시 "외부 배경지식"임을
    드러낼 수 있게 한다.

    Attributes:
        id: ``study_items.id`` (1 이상).
        topic_id: 소속 ``StudyTopic.id``.
        title: 항목 제목.
        text: 항목 본문(요약/사실 한 건).
        source_url: 출처 URL.
        source_title: 출처 표시 이름.
        status: :class:`StudyItemStatus` 값 문자열.
        confidence: 0.0~1.0 신뢰도.
        importance: 0.0~1.0 중요도.
        published_at: 원문 발행 시각(ISO8601) 또는 ``None``.
        retrieved_at: 수집 시각(ISO8601, freshness 기준).
        valid_until: 명시적 만료 시각(ISO8601) 또는 ``None``.
        metadata: 확장용 자유 형식 메타데이터.
        embedding: semantic search 용 임베딩 BLOB(후속 단계) 또는 ``None``.
    """

    id: int
    topic_id: str
    title: str
    text: str
    source_url: str
    source_title: str
    status: str
    confidence: float
    importance: float
    published_at: str | None
    retrieved_at: str
    valid_until: str | None
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: bytes | None = None
