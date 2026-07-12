"""Agent Study daily evolution 을 위한 관심 신호 provider 계층.

daily runner 가 대화/메모리 저장소 내부 스키마에 직접 의존하지 않고 최근 관심
신호를 얻기 위한 경계다. runner 는 :class:`StudySignalProvider` Protocol 만 알고,
실제 데이터 소스(대화 저장소, Dreaming 산출물, cron 리포트)는 주입식 provider 가
감싼다.

설계 결정:
- **storage-backed provider 는 콜백 주입식으로만.** 안정된 store API 가 확정되기
  전까지 private DB 스키마에 직접 의존하지 않는다(계획 Risk 3). 대신
  :class:`SourceBackedSignalProvider` 가 "최근 사용자 메시지/메모리 항목/insight/
  자동 산출물을 반환하는 zero-arg 콜백"만 받아 :func:`build_interest_signals_from_sources`
  로 정규화한다 — 어떤 store 든 read-only 콜백 하나로 붙일 수 있다.
- **가중치 정책은 interest_signals 가 SoT.** provider 는 신호를 모으고 정렬만
  하며, user > memory > auto_report 가중치 순서는
  :mod:`~simpleclaw.study.interest_signals` 의 정책 상수를 그대로 따른다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from simpleclaw.study.interest_signals import InterestSignal, extract_topic_hints


@runtime_checkable
class StudySignalProvider(Protocol):
    """topic evolution 에 쓸 최근 관심 신호를 수집하는 인터페이스."""

    def collect(self) -> list[InterestSignal]: ...


@dataclass
class StaticStudySignalProvider:
    """미리 구성된 신호를 반환하는 provider(테스트/no-op 용)."""

    signals: Iterable[InterestSignal] = field(default_factory=tuple)

    def collect(self) -> list[InterestSignal]:
        """구성된 신호를 리스트로 반환한다."""
        return list(self.signals)


def build_interest_signals_from_sources(
    *,
    user_messages: Sequence[Any] = (),
    memory_items: Iterable[Any] = (),
    insights: Iterable[Any] = (),
    auto_reports: Sequence[Any] = (),
) -> list[InterestSignal]:
    """이미 로드된 원천 데이터에서 관심 신호를 만든다(순수 빌더).

    :func:`~simpleclaw.study.interest_signals.extract_topic_hints` 의 얇은
    래퍼 — provider 계층이 저장소 read model 을 신호로 바꾸는 단일 진입점을
    고정해, 가중치 정책(user > auto_report)이 우회되지 않게 한다.
    """
    return extract_topic_hints(
        user_messages=user_messages,
        memory_items=memory_items,
        insights=insights,
        auto_reports=auto_reports,
    )


# 원천 데이터를 반환하는 zero-arg read-only 콜백 형태.
SourceFetcher = Callable[[], Sequence[Any]]


@dataclass
class SourceBackedSignalProvider:
    """read-only 콜백으로 원천 데이터를 당겨와 신호로 정규화하는 provider.

    store 객체가 아니라 콜백을 받는 이유: 대화/메모리 저장소의 안정된 조회 API 가
    확정되기 전까지 이 패키지가 store 내부 스키마에 결합되지 않게 하기 위함이다.
    runtime bridge 가 자기 환경에 맞는 콜백(예: ConversationStore 조회)을 만들어
    주입한다. 콜백 실패는 daily run 전체를 죽이지 않고 해당 원천만 건너뛴다.
    """

    fetch_user_messages: SourceFetcher | None = None
    fetch_memory_items: SourceFetcher | None = None
    fetch_insights: SourceFetcher | None = None
    fetch_auto_reports: SourceFetcher | None = None

    def collect(self) -> list[InterestSignal]:
        """모든 원천 콜백을 호출해 신호로 정규화한다(실패 원천은 빈 목록)."""
        return build_interest_signals_from_sources(
            user_messages=self._safe(self.fetch_user_messages),
            memory_items=self._safe(self.fetch_memory_items),
            insights=self._safe(self.fetch_insights),
            auto_reports=self._safe(self.fetch_auto_reports),
        )

    @staticmethod
    def _safe(fetcher: SourceFetcher | None) -> Sequence[Any]:
        """콜백 하나의 실패가 다른 원천 수집을 막지 않도록 격리한다."""
        if fetcher is None:
            return ()
        try:
            return fetcher() or ()
        except Exception:  # noqa: BLE001 — 원천별 격리가 목적(daily run 보호)
            return ()


def merge_signal_providers(
    providers: Iterable[StudySignalProvider],
) -> list[InterestSignal]:
    """여러 provider 의 신호를 합쳐 가중치 내림차순으로 정렬한다.

    빈 topic_hint 신호는 후보 topic 을 만들 수 없으므로 제거한다. 정렬 기준은
    interest_signals 의 extract_topic_hints 와 동일(가중치 → confidence → 원문).
    """
    signals: list[InterestSignal] = []
    for provider in providers:
        signals.extend(provider.collect())
    signals = [s for s in signals if s.topic_hint]
    signals.sort(key=lambda s: (-s.weight, -s.confidence, s.text))
    return signals
