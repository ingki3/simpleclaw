"""Study source collector 추상화와 fetch 요청/결과 데이터 모델.

설계 의도:
- source_planner 가 만든 :class:`StudyFetchRequest` 를 실제 외부 도구(news-search-skill,
  web_search, us-stock-skill 등)로 변환하는 책임은 collector 가 진다. 본 issue 단계에서는
  *아직 실제 도구를 호출하지 않는다* — 도구 wiring 은 후속 issue 의 몫이고, 여기서는
  테스트에서 자유롭게 mock/대체할 수 있는 인터페이스만 고정한다.
- 따라서 collector 는 좁은 :class:`StudyCollector` Protocol 로만 의존되며, 기본 구현인
  :class:`PlaceholderCollector` 는 결과 없이 "아직 미구현" limitation 만 남겨 파이프라인이
  end-to-end 로 돌아가되 wiki 에는 아무것도 쓰지 않도록 한다.
- 모든 데이터 모델은 ``frozen`` dataclass 로, 계획→수집→필터 단계 사이에서 불변으로
  전달된다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class StudyFetchRequest:
    """한 topic 을 한 collector 로 수집하기 위한 단일 요청.

    source_planner 가 topic × collector 조합마다 하나씩 생성한다. collector 구현은
    이 요청만 보고 외부 도구를 호출할 수 있어야 하므로, 도구가 필요로 하는 최소
    정보(query/신선도/개수 제한)를 모두 담는다.
    """

    topic_id: str
    query: str
    collector: str
    max_sources: int = 3
    freshness_hours: int = 24


@dataclass(frozen=True)
class StudyFetchResult:
    """collector 가 한 source 를 수집한 결과.

    relevance 필터와 wiki writer 가 공통으로 소비한다. ``confidence`` 는 collector 가
    매기는 수집 신뢰도(0.0~1.0)이고, ``limitations`` 는 "타임라인 검증 불가",
    "본문 일부만 추출" 같은 후속 단계가 알아야 할 한계를 사람이 읽을 문장으로 남긴다.
    """

    request: StudyFetchRequest
    title: str
    text: str
    url: str = ""
    source: str = ""
    published_at: str | None = None
    retrieved_at: str | None = None
    confidence: float = 0.0
    limitations: tuple[str, ...] = ()


@runtime_checkable
class StudyCollector(Protocol):
    """외부 도구를 감싸는 collector 의 최소 인터페이스.

    ``name`` 은 source policy 의 ``collectors`` 목록과 매칭되는 식별자이고,
    ``fetch`` 는 동기 호출로 요청을 0개 이상의 결과로 변환한다. 동기 시그니처를
    택한 이유는 테스트에서 단순 stub 으로 대체하기 쉽고, 비동기 도구는 어댑터에서
    감싸 노출하면 되기 때문이다.
    """

    name: str

    def fetch(self, request: StudyFetchRequest) -> Sequence[StudyFetchResult]: ...


@dataclass
class PlaceholderCollector:
    """아직 실제 도구가 연결되지 않은 collector 의 기본 구현.

    결과를 반환하지 않고(=wiki 에 아무것도 쓰지 않음) 빈 시퀀스를 돌려준다.
    후속 issue 에서 실제 도구 어댑터로 교체될 자리표시자이며, 그 전까지는 계획
    단계가 만든 요청이 안전하게 no-op 으로 흘러가게 한다.
    """

    name: str

    def fetch(self, request: StudyFetchRequest) -> Sequence[StudyFetchResult]:
        """실제 도구 호출 없이 빈 결과를 반환한다 (no-op)."""
        return ()


@dataclass
class CollectorRegistry:
    """collector 이름 → 구현 매핑.

    source_planner 가 만든 요청의 ``collector`` 필드로 구현을 찾아 실행하기 위한
    얇은 레지스트리. 등록되지 않은 collector 이름은 :meth:`get` 에서 기본
    :class:`PlaceholderCollector` 로 폴백해, 정책에 새 collector 이름이 추가돼도
    파이프라인이 깨지지 않고 no-op 으로 흐르게 한다.
    """

    collectors: dict[str, StudyCollector] = field(default_factory=dict)

    def register(self, collector: StudyCollector) -> None:
        """collector 를 ``collector.name`` 키로 등록(또는 교체)한다."""
        self.collectors[collector.name] = collector

    def get(self, name: str) -> StudyCollector:
        """이름으로 collector 를 조회한다. 미등록이면 placeholder 로 폴백."""
        return self.collectors.get(name, PlaceholderCollector(name))

    def fetch(self, request: StudyFetchRequest) -> Sequence[StudyFetchResult]:
        """요청의 collector 이름으로 구현을 찾아 ``fetch`` 를 위임한다."""
        return self.get(request.collector).fetch(request)

    def fetch_all(
        self, requests: Sequence[StudyFetchRequest]
    ) -> list[StudyFetchResult]:
        """여러 요청을 순서대로 수집해 평탄화한 결과 리스트를 반환한다."""
        results: list[StudyFetchResult] = []
        for request in requests:
            results.extend(self.fetch(request))
        return results
