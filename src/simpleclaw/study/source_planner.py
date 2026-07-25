"""topic → fetch 요청 계획 + 일반 뉴스 relevance 필터.

공부 대상은 두 갈래다. (1) 사용자가 관심을 보인 주제(``user_interest``), (2) 일반
뉴스 중 사용자가 관심 가질 법하거나 중요도가 높은 내용(``general_news``). 둘 다
무작정 수집하면 noise 가 커지므로, 이 모듈은:

1. topic 의 category 에 맞는 *source policy* 를 적용해 collector 별 fetch 요청을
   생성하고(:func:`plan_fetch_requests`),
2. 수집된 결과 중 ``general_news`` 후보는 relevance score 가 임계값 미만이면
   wiki 에 쓰지 않도록 걸러낸다(:func:`select_wiki_worthy`).

설계 결정:
- topic 자료구조는 후속/선행 issue 의 ``topic_registry`` 에 하드 의존하지 않도록
  :class:`StudyTopic` Protocol 로만 받는다. 필요한 최소 필드만 요구한다.
- relevance 판정 기준은 코드에 박지 않고 ``prompts/study/news_relevance.yaml`` 에서
  관리한다(프로젝트 프롬프트 SoT 규칙). 실제 LLM 호출은 후속 issue 에서 주입되며,
  본 단계의 기본 :class:`ConfidenceRelevanceScorer` 는 collector 신뢰도 기반의
  결정적(휴리스틱) scorer 로, 테스트에서 자유롭게 다른 scorer 로 대체할 수 있다.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

import yaml

from simpleclaw.study.collectors import StudyFetchRequest, StudyFetchResult

logger = logging.getLogger(__name__)

# general_news 후보를 wiki 에 쓸지 가르는 기본 임계값. user_interest 는 적용 안 함.
DEFAULT_RELEVANCE_THRESHOLD: float = 0.5


class TopicKind(StrEnum):
    """study topic 의 출처 갈래.

    ``USER_INTEREST`` 는 사용자가 명시적으로 관심을 보인 주제로 relevance gate 를
    통과하고, ``GENERAL_NEWS`` 는 사용자가 요청하지 않은 일반 뉴스라 relevance
    필터를 거쳐야 wiki 에 기록된다.
    """

    USER_INTEREST = "user_interest"
    GENERAL_NEWS = "general_news"


@runtime_checkable
class StudyTopic(Protocol):
    """source planner 가 요구하는 topic 의 최소 인터페이스.

    선행 issue 의 ``topic_registry`` 가 제공할 Topic 객체를 직접 import 하지 않고
    duck typing 으로 받기 위한 Protocol. 테스트는 동일 필드를 가진 임의 객체를
    넘길 수 있다.
    """

    topic_id: str
    label: str
    category: str
    kind: TopicKind
    max_sources: int
    freshness_hours: int


def _topic_queries(topic: StudyTopic) -> tuple[str, ...]:
    """topic 이 collector 에 던질 검색 쿼리 목록을 정한다.

    ``search_queries`` 가 있으면 그것을 쓰고, 없으면 label 로 폴백한다.
    ``market-reports-us-kr`` 처럼 display label 이 검색어로 부적합한 topic 이
    label 과 검색 쿼리를 분리하는 통로다(BIZ-434). ``getattr`` 폴백을 쓰는 이유:
    Protocol 에 필드를 강제하면 기존 테스트 stub/legacy topic 객체가 모두 깨지므로
    optional duck-typing 으로 수용한다.
    """
    queries = getattr(topic, "search_queries", None)
    if isinstance(queries, list):
        cleaned = tuple(q.strip() for q in queries if isinstance(q, str) and q.strip())
        if cleaned:
            return cleaned
    return (topic.label,)


@dataclass(frozen=True)
class CategorySourcePolicy:
    """한 category 의 수집 정책.

    ``collectors`` 는 우선순위 순서의 collector 이름 목록이고,
    ``preferred_domains`` 는 신뢰 도메인 힌트(현 단계에서는 메타데이터로만 보존),
    ``require_timeline_validation`` 은 스포츠처럼 타임라인 검증이 필수인 category 에
    True 로 표시해 후속 단계가 강제하도록 한다.
    """

    collectors: tuple[str, ...]
    preferred_domains: tuple[str, ...] = ()
    require_timeline_validation: bool = False


@dataclass(frozen=True)
class SourcePolicy:
    """category → :class:`CategorySourcePolicy` 매핑과 폴백 정책.

    알려지지 않은 category 의 topic 은 ``fallback`` 정책으로 수집한다.
    """

    categories: Mapping[str, CategorySourcePolicy]
    fallback: CategorySourcePolicy

    def for_category(self, category: str) -> CategorySourcePolicy:
        """category 에 맞는 정책을 반환하고, 없으면 fallback 을 쓴다."""
        return self.categories.get(category, self.fallback)


# 운영 기본 source policy. config 로 덮어쓸 수 있도록 load_source_policy 가 제공된다.
DEFAULT_SOURCE_POLICY: SourcePolicy = SourcePolicy(
    categories={
        "ai-industry": CategorySourcePolicy(
            collectors=("news-search-skill", "web_search"),
            preferred_domains=(
                "openai.com",
                "anthropic.com",
                "blog.google",
                "semianalysis.com",
                "nytimes.com",
                "bloomberg.com",
            ),
        ),
        "markets": CategorySourcePolicy(
            collectors=("us-stock-skill", "kr-stock-skill", "news-search-skill"),
        ),
        "sports": CategorySourcePolicy(
            collectors=("realtime-lookup-skill", "web_search"),
            require_timeline_validation=True,
        ),
    },
    fallback=CategorySourcePolicy(collectors=("news-search-skill", "web_search")),
)


def load_source_policy(mapping: Mapping[str, object]) -> SourcePolicy:
    """config dict 로부터 :class:`SourcePolicy` 를 만든다.

    기대하는 구조::

        default_sources:
          ai-industry:
            collectors: [news-search-skill, web_search]
            preferred_domains: [openai.com, ...]
            require_timeline_validation: false
        fallback:
          collectors: [news-search-skill, web_search]

    ``default_sources`` 키가 있으면 그 하위를 category 맵으로 쓰고, 없으면 mapping
    자체를 category 맵으로 본다. ``fallback`` 이 없으면 기본 fallback 을 사용한다.
    """
    categories_raw = mapping.get("default_sources", mapping)
    if not isinstance(categories_raw, Mapping):
        raise TypeError("source policy: 'default_sources' must be a mapping")

    categories: dict[str, CategorySourcePolicy] = {}
    for name, raw in categories_raw.items():
        if name == "fallback":
            continue
        categories[str(name)] = _build_category_policy(str(name), raw)

    fallback_raw = mapping.get("fallback")
    fallback = (
        _build_category_policy("fallback", fallback_raw)
        if fallback_raw is not None
        else DEFAULT_SOURCE_POLICY.fallback
    )
    return SourcePolicy(categories=categories, fallback=fallback)


def _build_category_policy(name: str, raw: object) -> CategorySourcePolicy:
    """category 한 항목의 raw dict 를 :class:`CategorySourcePolicy` 로 변환한다."""
    if not isinstance(raw, Mapping):
        raise TypeError(f"source policy: category {name!r} must be a mapping")
    collectors = tuple(str(c) for c in raw.get("collectors", ()))
    if not collectors:
        raise ValueError(f"source policy: category {name!r} has no collectors")
    preferred = tuple(str(d) for d in raw.get("preferred_domains", ()))
    require_timeline = bool(raw.get("require_timeline_validation", False))
    return CategorySourcePolicy(
        collectors=collectors,
        preferred_domains=preferred,
        require_timeline_validation=require_timeline,
    )


def plan_fetch_requests(
    topics: Iterable[StudyTopic],
    *,
    policy: SourcePolicy = DEFAULT_SOURCE_POLICY,
) -> list[StudyFetchRequest]:
    """topic 들로부터 collector 별 fetch 요청을 생성한다.

    topic 하나당, 검색 쿼리(:func:`_topic_queries` — ``search_queries`` 우선,
    없으면 label)마다 그 category 정책에 나열된 collector 별 요청을 만든다.
    같은 쿼리 안에서 collector 이름이 중복되면 한 번만 생성한다(정책 오타 방어).

    Args:
        topics: 계획 대상 topic 들. :class:`StudyTopic` 인터페이스만 충족하면 된다.
        policy: 적용할 source policy. 기본은 :data:`DEFAULT_SOURCE_POLICY`.

    Returns:
        topic × query × collector 순서가 보존된 :class:`StudyFetchRequest` 리스트.
    """
    requests: list[StudyFetchRequest] = []
    for topic in topics:
        category_policy = policy.for_category(topic.category)
        for query in _topic_queries(topic):
            seen: set[str] = set()
            for collector in category_policy.collectors:
                if collector in seen:
                    continue
                seen.add(collector)
                requests.append(
                    StudyFetchRequest(
                        topic_id=topic.topic_id,
                        query=query,
                        collector=collector,
                        max_sources=topic.max_sources,
                        freshness_hours=topic.freshness_hours,
                    )
                )
    return requests


@dataclass(frozen=True)
class RelevanceAssessment:
    """relevance scorer 의 판정 결과.

    ``score`` 는 0.0~1.0, ``should_study`` 는 scorer 자체 판단(임계값과 별개로
    scorer 가 강한 신호를 가질 때 사용), ``reasons`` 는 사람이 읽을 근거.
    """

    score: float
    should_study: bool
    reasons: tuple[str, ...] = ()


@runtime_checkable
class RelevanceScorer(Protocol):
    """일반 뉴스 후보의 relevance 를 매기는 인터페이스.

    실제 구현은 후속 issue 에서 ``news_relevance.yaml`` 프롬프트로 LLM 을 호출할
    수 있다. 본 단계의 기본 구현은 휴리스틱이며, 테스트는 stub scorer 로 대체한다.
    """

    def score(
        self, result: StudyFetchResult, *, topic: StudyTopic
    ) -> RelevanceAssessment: ...


@dataclass(frozen=True)
class ConfidenceRelevanceScorer:
    """collector 신뢰도 기반의 결정적 기본 scorer.

    실제 LLM scorer 가 붙기 전까지 파이프라인이 돌아가게 하는 휴리스틱이다.
    수집 결과의 ``confidence`` 를 그대로 relevance 로 쓰되, ``limitations`` 가
    있으면(예: 타임라인 검증 불가) 신뢰를 한 단계 깎는다. 본문이 비어 있으면
    공부할 내용이 없으므로 0점.
    """

    limitation_penalty: float = 0.2

    def score(
        self, result: StudyFetchResult, *, topic: StudyTopic
    ) -> RelevanceAssessment:
        """confidence − limitation 패널티로 relevance 를 산출한다."""
        if not result.text.strip():
            return RelevanceAssessment(
                score=0.0, should_study=False, reasons=("본문 없음",)
            )
        reasons: list[str] = []
        score = result.confidence
        if result.limitations:
            score -= self.limitation_penalty
            reasons.append(f"한계 {len(result.limitations)}건으로 신뢰 감점")
        score = max(0.0, min(1.0, score))
        reasons.append(f"수집 신뢰도 {result.confidence:.2f}")
        # should_study 는 임계값과 독립적으로 scorer 의 약한 추천만 표현한다.
        return RelevanceAssessment(
            score=score, should_study=score >= 0.5, reasons=tuple(reasons)
        )


@dataclass(frozen=True)
class WikiSelection:
    """relevance 필터 결과: 채택/탈락을 분리해 후속 로깅/감사가 쓰게 한다."""

    selected: tuple[StudyFetchResult, ...]
    rejected: tuple[tuple[StudyFetchResult, RelevanceAssessment], ...]


def select_wiki_worthy(
    results: Sequence[StudyFetchResult],
    *,
    topics: Mapping[str, StudyTopic],
    scorer: RelevanceScorer | None = None,
    threshold: float = DEFAULT_RELEVANCE_THRESHOLD,
) -> WikiSelection:
    """수집 결과 중 wiki 에 기록할 가치가 있는 것만 고른다.

    - ``user_interest`` topic 의 결과는 사용자가 이미 관심을 표명했으므로 relevance
      gate 없이 통과한다.
    - ``general_news`` 후보는 scorer 로 점수를 매겨, ``score >= threshold`` 이고
      scorer 가 ``should_study`` 일 때만 채택한다(둘 다 만족해야 noise 차단).
    - topic 매핑에 없는 결과는 안전하게 ``general_news`` 로 간주해 필터를 적용한다.

    Args:
        results: collector 가 반환한 수집 결과들.
        topics: ``topic_id`` → topic 매핑. 각 결과의 kind 판정에 쓰인다.
        scorer: relevance scorer. ``None`` 이면 :class:`ConfidenceRelevanceScorer`.
        threshold: general_news 채택 임계값(0.0~1.0).

    Returns:
        채택/탈락이 분리된 :class:`WikiSelection`.
    """
    active_scorer = scorer or ConfidenceRelevanceScorer()
    selected: list[StudyFetchResult] = []
    rejected: list[tuple[StudyFetchResult, RelevanceAssessment]] = []

    for result in results:
        topic = topics.get(result.request.topic_id)
        # 알 수 없는 topic 은 보수적으로 일반 뉴스로 보고 gate 를 적용한다.
        kind = topic.kind if topic is not None else TopicKind.GENERAL_NEWS

        if kind == TopicKind.USER_INTEREST:
            selected.append(result)
            continue

        assessment = active_scorer.score(result, topic=topic)
        if assessment.score >= threshold and assessment.should_study:
            selected.append(result)
        else:
            rejected.append((result, assessment))

    return WikiSelection(selected=tuple(selected), rejected=tuple(rejected))


# --------------------------------------------------------------------------- #
# news_relevance.yaml 로더
#
# prompt SoT 는 repo 루트의 prompts/study/news_relevance.yaml. dreaming 프롬프트
# 로더(memory.prompt_loader)와 동일한 repo-root 해소 규칙을 따르되, study 전용
# 경량 spec 만 제공한다. 실제 LLM scorer 가 붙는 후속 issue 에서 이 spec 을
# format() 하여 사용한다.
# --------------------------------------------------------------------------- #

_REPO_ROOT_ENV = "SIMPLECLAW_ROOT"
_REPO_ROOT_MARKER = "pyproject.toml"
_PROMPT_SUBPATH = ("prompts", "study")
_FORMAT_FIELD_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class StudyPromptError(RuntimeError):
    """study 프롬프트 로드/검증 실패 (fail-closed)."""


@dataclass(frozen=True)
class StudyPromptSpec:
    """study 프롬프트(system + user template)의 메모리 표현."""

    name: str
    version: int
    description: str
    system_prompt: str
    user_prompt: str
    required_vars: tuple[str, ...]
    source_path: Path

    def format(self, **kwargs: object) -> str:
        """user_prompt 를 채운다. required_vars 누락/미선언 placeholder 시 오류."""
        missing = [v for v in self.required_vars if v not in kwargs]
        if missing:
            raise StudyPromptError(
                f"study prompt {self.name!r} missing required vars: {missing}"
            )
        try:
            return self.user_prompt.format(**kwargs)
        except KeyError as exc:
            raise StudyPromptError(
                f"study prompt {self.name!r} format failed (undeclared placeholder): {exc}"
            ) from exc


def load_news_relevance_prompt(
    *, repo_root: str | Path | None = None
) -> StudyPromptSpec:
    """``<repo_root>/prompts/study/news_relevance.yaml`` 을 로드/검증한다."""
    return _load_study_prompt("news_relevance", repo_root=repo_root)


def _load_study_prompt(
    name: str, *, repo_root: str | Path | None
) -> StudyPromptSpec:
    root = _resolve_repo_root(repo_root)
    path = root.joinpath(*_PROMPT_SUBPATH, f"{name}.yaml")
    if not path.is_file():
        raise StudyPromptError(f"study prompt {name!r} not found at {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise StudyPromptError(
            f"study prompt {name!r}: could not read/parse {path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise StudyPromptError(f"study prompt {name!r}: root must be a mapping at {path}")

    for required_field in ("system_prompt", "user_prompt"):
        if not isinstance(data.get(required_field), str):
            raise StudyPromptError(
                f"study prompt {name!r}: {required_field!r} must be a string at {path}"
            )

    user_prompt = data["user_prompt"]
    required_vars = tuple(str(v) for v in (data.get("required_vars") or []))
    declared = set(required_vars)
    actual = _extract_format_vars(user_prompt)
    if declared != actual:
        raise StudyPromptError(
            f"study prompt {name!r}: required_vars do not match placeholders at {path} — "
            f"missing_in_declared={sorted(actual - declared)}, "
            f"extra_in_declared={sorted(declared - actual)}"
        )

    return StudyPromptSpec(
        name=name,
        version=int(data.get("version", 1)),
        description=str(data.get("description", "")),
        system_prompt=data["system_prompt"],
        user_prompt=user_prompt,
        required_vars=required_vars,
        source_path=path,
    )


def _extract_format_vars(text: str) -> set[str]:
    """``str.format`` placeholder 이름 집합. ``{{``/``}}`` 이스케이프는 제외."""
    safe = text.replace("{{", "\x00").replace("}}", "\x01")
    return {m.group(1) for m in _FORMAT_FIELD_RE.finditer(safe)}


def _resolve_repo_root(repo_root: str | Path | None) -> Path:
    """repo root 해소: override > SIMPLECLAW_ROOT env > pyproject.toml walk-up."""
    if repo_root is not None:
        return Path(repo_root).expanduser().resolve()
    env_value = os.environ.get(_REPO_ROOT_ENV)
    if env_value:
        return Path(env_value).expanduser().resolve()
    start = Path(__file__).resolve()
    for candidate in (start, *start.parents):
        if (candidate / _REPO_ROOT_MARKER).is_file():
            return candidate
    raise StudyPromptError(
        f"could not resolve repo root: {_REPO_ROOT_ENV} unset and no "
        f"{_REPO_ROOT_MARKER} found walking up from {start}"
    )
