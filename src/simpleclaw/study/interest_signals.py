"""Dreaming/대화 기반 관심사 signal 추출 — study topic 의 seed 를 만든다.

공부 대상은 고정 리스트가 아니라 사용자가 물었거나 관심을 보인 주제에서 진화해야
한다. 이 모듈은 SimpleClaw 가 이미 가진 신호들(Dreaming 산출물, 대화 클러스터,
승격된 insight, 최근 사용자 질문)을 읽어 :class:`InterestSignal` 로 정규화한다.
후속 issue 의 ``topic_evolution`` 이 이 signal 을 모아 후보 topic 을 만들고 관심도
임계를 적용한다.

핵심 설계 결정:

- **과대 일반화 방어가 1차 목표다.** cron/recipe 가 자동으로 만든 산출물(일반 뉴스
  브리핑 등)이 한 번 스쳤다고 "사용자 관심사"로 굳으면 안 된다. 따라서 자동 산출물
  (``auto_report``)은 항상 낮은 가중치(:data:`AUTO_REPORT_MAX_WEIGHT` 미만)를 받고,
  사용자가 organic 하게 던진 질문은 더 높은 가중치를 받는다. 같은 질문이 반복되면
  가중치/신뢰도가 올라간다.
- **읽기 전용이다.** 이 모듈은 메모리/insight 를 *읽기만* 하고 어떤 사용자 메모리
  파일에도 쓰지 않는다(설계 문서의 비목표 §4). 따라서 store 객체에 직접 의존하지
  않고, 이미 로드된 plain dict/dataclass 를 duck typing 으로 받는다.
- signal 마다 ``source``/``weight``/``confidence``/``source_ref`` 를 남겨 후속 단계가
  왜 이 topic 이 후보가 됐는지 감사할 수 있게 한다(DoD §3).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InterestSignal:
    """관심사 topic 의 seed 가 되는 단일 신호.

    Attributes:
        topic_hint: 이 신호에서 도출한 짧은 주제 힌트(후보 topic 의 label 후보).
        text: 신호의 원문(요약/질문 등). 감사/디버깅용 근거.
        source: 신호 출처 종류. :data:`SOURCE_WEIGHTS` 의 키 중 하나.
        source_ref: 원본 식별자(memory item id, insight topic, 메시지 인덱스 등).
        weight: 관심도 가중치(0.0~1.0). organic 사용자 신호일수록 높고, 자동
            산출물일수록 낮다. topic_evolution 의 관심도 누적에 쓰인다.
        confidence: 신호 자체의 신뢰도(0.0~1.0). 반복/승격될수록 높다.
        last_seen: 마지막 관측 시각(ISO 문자열). 알 수 없으면 ``None``.
    """

    topic_hint: str
    text: str
    source: str
    source_ref: str = ""
    weight: float = 0.0
    confidence: float = 0.0
    last_seen: str | None = None


# --------------------------------------------------------------------------- #
# 가중치 정책
#
# organic 사용자 신호 > 메모리 승격물 > 자동 산출물 순서를 코드 한 곳에서 본다.
# 자동 산출물은 AUTO_REPORT_MAX_WEIGHT 미만으로 강제 클램프되어, 과대 일반화를
# 구조적으로 막는다(DoD §2).
# --------------------------------------------------------------------------- #

# memory_items 중 관심 신호로 인정하는 type → 기본 가중치.
# 설계 계획이 지정한 네 종류만 채택한다(나머지 type 은 관심사 신호로 보지 않음).
MEMORY_ITEM_WEIGHTS: dict[str, float] = {
    "accepted_user_insight": 0.8,  # 사용자가 승인한 1인칭 insight — 가장 강한 신호
    "active_project": 0.75,  # 현재 진행 중인 프로젝트 — 지속 관심
    "decision": 0.6,  # 사용자의 결정 — 관련 배경지식 수요
    "cluster_summary": 0.55,  # 반복 대화 묶음 — 중간 강도 신호
}

# 한 줄짜리 organic 사용자 질문의 기본 가중치. 반복 시 REPEAT_WEIGHT_STEP 만큼 가산.
USER_MESSAGE_BASE_WEIGHT: float = 0.6
REPEAT_WEIGHT_STEP: float = 0.12
USER_MESSAGE_MAX_WEIGHT: float = 0.95

# insights.jsonl 의 promoted/고신뢰 insight 기본 가중치.
INSIGHT_BASE_WEIGHT: float = 0.7

# 자동 산출물(cron/recipe 뉴스 브리핑 등)의 상한. 이 미만으로만 가중치를 부여해
# "본 적 있음"이 "관심사"로 승격되지 않게 한다.
AUTO_REPORT_MAX_WEIGHT: float = 0.3

# 자동 산출물 외 신호로 인정할 insight 의 최소 confidence(고신뢰만 채택).
INSIGHT_MIN_CONFIDENCE: float = 0.6


# --------------------------------------------------------------------------- #
# topic hint 추출
# --------------------------------------------------------------------------- #

# 사용자 질문/요청문 끝에 흔히 붙는 명령형 표현. topic hint 에서 제거해 주제만 남긴다.
_REQUEST_TAIL_RE = re.compile(
    r"(?:"
    r"조사|분석|정리|요약|설명|확인|검색|찾|알아봐|알려|찾아봐|찾아|봐줘"
    r")"
    r"(?:해|해서|해봐|봐|줘|주세요|주라|줄래|드려|드릴게)*"
    r"(?:요|용)?[\s\.\!\?~]*$"
)

# 한국어 종결 어미/군더더기 꼬리. 클러스터 요약 등 서술문에서 제거.
_NARRATIVE_TAIL_RE = re.compile(
    r"(?:이|가|을|를|에|에서|으로|로|와|과|이런|관련)?\s*"
    r"(?:반복(?:됨|된다|되고\s*있음)?|논의(?:됨|중)?|질문(?:이\s*반복(?:됨)?)?)"
    r"[\s\.\!\?~]*$"
)

# 고유명사 후보: 대문자 ASCII 를 포함한 토큰(OpenAI, AI, GPT, NVDA 등).
_PROPER_NOUN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\.\-]*[A-Z][A-Za-z0-9\.\-]*|[A-Z]{2,}")

# topic hint 의 최대 길이(label 후보로 쓰기 적당한 한 줄).
_MAX_HINT_LEN: int = 80


def derive_topic_hint(text: str) -> str:
    """원문에서 짧은 주제 힌트를 만든다.

    명령형 꼬리("...조사해줘")와 서술 꼬리("...반복됨")를 떼어 주제 핵심만 남기고,
    너무 길면 첫 절 기준으로 줄인다. 고유명사(OpenAI 등)가 잘려나가지 않도록
    절 분리는 보수적으로 한다.

    Args:
        text: 사용자 질문/메모리 요약/insight 본문 등의 원문.

    Returns:
        주제 힌트 문자열. 의미 있는 토큰이 없으면 정리된 원문(혹은 빈 문자열).
    """
    cleaned = " ".join(text.split())
    if not cleaned:
        return ""

    # 명령형/서술 꼬리는 주제가 아니므로 반복 제거(중첩된 경우 대비).
    prev = None
    while prev != cleaned:
        prev = cleaned
        cleaned = _REQUEST_TAIL_RE.sub("", cleaned).strip()
        cleaned = _NARRATIVE_TAIL_RE.sub("", cleaned).strip()
    cleaned = cleaned.strip(" \t.,!?~·-")

    if not cleaned:
        # 꼬리만 있던 문장이면 원문 정리본으로 폴백한다.
        cleaned = " ".join(text.split())

    if len(cleaned) <= _MAX_HINT_LEN:
        return cleaned
    # 길면 첫 문장 부호 또는 길이 기준으로 자른다.
    head = re.split(r"[\.!?\n]", cleaned, maxsplit=1)[0].strip()
    if head and len(head) <= _MAX_HINT_LEN:
        return head
    return cleaned[:_MAX_HINT_LEN].rstrip()


def extract_keywords(text: str) -> list[str]:
    """주제 비교/그룹화에 쓸 핵심 키워드를 뽑는다.

    고유명사(대문자 포함 토큰)를 우선 보존하고, 그 외에는 2자 이상의 한글/영문
    단어를 길이순으로 추린다. 반복 질문 탐지의 grouping key 로 사용한다.
    """
    proper = _PROPER_NOUN_RE.findall(text)
    words = re.findall(r"[A-Za-z][A-Za-z0-9\.\-]+|[가-힣]{2,}", text)
    seen: set[str] = set()
    keywords: list[str] = []
    for token in (*proper, *words):
        norm = token.lower()
        if norm in seen or len(token) < 2:
            continue
        seen.add(norm)
        keywords.append(token)
    return keywords


# 반복 질문으로 묶을 키워드 포함도 임계값. 작은 쪽 키워드 집합이 큰 쪽에
# 이 비율 이상 포함되면 같은 주제로 본다(표현이 달라도 핵심어가 겹치면 반복).
_REPEAT_CONTAINMENT_THRESHOLD: float = 0.6


def _keyword_set(text: str) -> frozenset[str]:
    """반복 질문 탐지용 정규화 키워드 집합."""
    keywords = extract_keywords(text)
    if not keywords:
        # 키워드가 없으면 공백/구두점 제거한 소문자 원문 한 덩어리로 폴백.
        return frozenset({re.sub(r"[\s\W_]+", "", text.lower())})
    return frozenset(k.lower() for k in keywords)


def _same_topic(a: frozenset[str], b: frozenset[str]) -> bool:
    """두 키워드 집합이 같은 주제(반복 질문)인지 포함도로 판정한다.

    작은 집합이 큰 집합에 충분히 포함되면(예: "테슬라 주가" ⊂ "테슬라 주가 어떻게
    되고 있어") 같은 주제로 본다. 교집합이 전혀 없으면 다른 주제.
    """
    if not a or not b:
        return False
    overlap = len(a & b)
    if overlap == 0:
        return False
    return overlap / min(len(a), len(b)) >= _REPEAT_CONTAINMENT_THRESHOLD


# --------------------------------------------------------------------------- #
# 입력 정규화 헬퍼 (dict 와 dataclass 를 모두 duck typing 으로 수용)
# --------------------------------------------------------------------------- #


def _field(obj: Any, key: str, default: Any = None) -> Any:
    """Mapping 이면 key 로, 객체면 attribute 로 값을 읽는다(없으면 default)."""
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _as_type_str(value: Any) -> str:
    """memory item 의 type 을 문자열로 정규화한다(Enum 이면 .value)."""
    if value is None:
        return ""
    # MemoryItemType 같은 Enum 은 .value 가 논리 타입 문자열.
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)


def _as_text(item: Any) -> str:
    """문자열이면 그대로, dict/객체면 ``text`` 필드를 본문으로 본다."""
    if isinstance(item, str):
        return item
    return str(_field(item, "text", "") or "")


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """value 를 [low, high] 로 클램프."""
    return max(low, min(high, value))


# --------------------------------------------------------------------------- #
# source 별 signal 빌더
# --------------------------------------------------------------------------- #


def signals_from_user_messages(
    user_messages: Sequence[Any],
) -> list[InterestSignal]:
    """최근 사용자 메시지에서 organic 관심 신호를 만든다.

    같은 주제(키워드 집합)가 반복되면 가중치/신뢰도를 올린다 — 반복 질문이 곧
    강한 관심 신호이기 때문이다. 메시지는 문자열 또는 ``{"text": ...}`` dict 를
    허용한다.
    """
    # 키워드 포함도 기반 greedy 그룹핑. 각 그룹은 (대표 idx, 대표 원문, 키워드,
    # 등장 횟수). 새 메시지는 기존 그룹과 같은 주제면 횟수만 올리고, 아니면 새
    # 그룹을 연다. 대표는 먼저 등장한(가장 이른 idx) 메시지를 유지한다.
    groups: list[list] = []  # [idx, text, frozenset, count]
    for idx, raw in enumerate(user_messages):
        text = _as_text(raw).strip()
        if not text:
            continue
        keywords = _keyword_set(text)
        for group in groups:
            if _same_topic(keywords, group[2]):
                group[3] += 1
                # 대표 키워드는 누적해 후속 비교의 회상력을 높인다.
                group[2] = group[2] | keywords
                break
        else:
            groups.append([idx, text, keywords, 1])

    signals: list[InterestSignal] = []
    for idx, text, _keywords, count in groups:
        # 반복할수록 가중치/신뢰도 상승(상한 클램프).
        weight = _clamp(
            USER_MESSAGE_BASE_WEIGHT + REPEAT_WEIGHT_STEP * (count - 1),
            high=USER_MESSAGE_MAX_WEIGHT,
        )
        confidence = _clamp(0.35 + 0.15 * (count - 1))
        signals.append(
            InterestSignal(
                topic_hint=derive_topic_hint(text),
                text=text,
                source="user_message",
                source_ref=f"user_message[{idx}]"
                + (f" x{count}" if count > 1 else ""),
                weight=weight,
                confidence=confidence,
            )
        )
    return signals


def signals_from_memory_items(
    memory_items: Iterable[Any],
) -> list[InterestSignal]:
    """memory_items 중 관심사로 인정되는 type 만 신호로 변환한다.

    채택 type 은 :data:`MEMORY_ITEM_WEIGHTS` 의 키 — accepted_user_insight,
    active_project, decision, cluster_summary. 그 외 type(memory/user/suggestion
    등)은 외부 세계 관심사 신호로 보지 않고 건너뛴다.
    """
    signals: list[InterestSignal] = []
    for item in memory_items:
        item_type = _as_type_str(_field(item, "type", ""))
        base_weight = MEMORY_ITEM_WEIGHTS.get(item_type)
        if base_weight is None:
            continue  # 관심 신호로 인정하지 않는 type
        text = _as_text(item).strip()
        if not text:
            continue

        # item 자체 confidence 가 있으면 가중치를 소폭 반영해 약한 신호를 눌러준다.
        item_conf = _coerce_float(_field(item, "confidence", None))
        weight = base_weight
        if item_conf is not None:
            weight = _clamp(base_weight * (0.7 + 0.3 * _clamp(item_conf)))

        signals.append(
            InterestSignal(
                topic_hint=derive_topic_hint(text),
                text=text,
                source=item_type,
                source_ref=str(_field(item, "source_ref", "") or _field(item, "id", "") or ""),
                weight=weight,
                confidence=_clamp(item_conf if item_conf is not None else 0.5),
                last_seen=_coerce_timestamp(_field(item, "last_seen", None)),
            )
        )
    return signals


def signals_from_insights(
    insights: Iterable[Any],
    *,
    min_confidence: float = INSIGHT_MIN_CONFIDENCE,
) -> list[InterestSignal]:
    """insights.jsonl 의 promoted/고신뢰 insight 만 신호로 변환한다.

    ``is_promoted`` 가 True 이거나 confidence 가 ``min_confidence`` 이상인 항목만
    채택한다 — 단발 관측(저신뢰)이 관심사로 새는 것을 막는다. insight 는
    ``InsightMeta`` dataclass 또는 동일 필드를 가진 dict 를 허용한다.
    """
    signals: list[InterestSignal] = []
    for insight in insights:
        confidence = _coerce_float(_field(insight, "confidence", 0.0)) or 0.0
        promoted = bool(_field(insight, "is_promoted", False))
        if not promoted and confidence < min_confidence:
            continue
        text = _as_text(insight).strip()
        topic = str(_field(insight, "topic", "") or "")
        if not text and not topic:
            continue
        hint_source = text or topic
        # promoted insight 는 base 가중치를 그대로, 그 외 고신뢰는 confidence 로 스케일.
        weight = INSIGHT_BASE_WEIGHT if promoted else _clamp(INSIGHT_BASE_WEIGHT * confidence)
        signals.append(
            InterestSignal(
                topic_hint=derive_topic_hint(hint_source),
                text=text or topic,
                source="insight",
                source_ref=topic,
                weight=weight,
                confidence=_clamp(confidence),
                last_seen=_coerce_timestamp(_field(insight, "last_seen", None)),
            )
        )
    return signals


def signals_from_auto_reports(
    auto_reports: Sequence[Any],
) -> list[InterestSignal]:
    """cron/recipe 자동 산출물에서 낮은 가중치 신호를 만든다.

    auto-trigger 채널의 산출물은 사용자가 능동적으로 요청한 게 아니므로 항상
    :data:`AUTO_REPORT_MAX_WEIGHT` 미만으로 클램프한다. topic_evolution 이 이
    신호만으로는 후보를 ``active`` 로 승격하지 못하게 하는 구조적 방어선이다.
    """
    signals: list[InterestSignal] = []
    for idx, raw in enumerate(auto_reports):
        text = _as_text(raw).strip()
        if not text:
            continue
        # 상한 미만으로 강제 — 절대 0.5(사용자 신호 영역)에 닿지 않게 한다.
        weight = min(AUTO_REPORT_MAX_WEIGHT - 0.05, AUTO_REPORT_MAX_WEIGHT)
        signals.append(
            InterestSignal(
                topic_hint=derive_topic_hint(text),
                text=text,
                source="auto_report",
                source_ref=f"auto_report[{idx}]",
                weight=_clamp(weight, high=AUTO_REPORT_MAX_WEIGHT - 1e-9),
                confidence=0.1,
            )
        )
    return signals


# --------------------------------------------------------------------------- #
# 오케스트레이터
# --------------------------------------------------------------------------- #


def extract_topic_hints(
    *,
    user_messages: Sequence[Any] = (),
    memory_items: Iterable[Any] = (),
    insights: Iterable[Any] = (),
    auto_reports: Sequence[Any] = (),
) -> list[InterestSignal]:
    """모든 입력 신호를 모아 :class:`InterestSignal` 리스트로 정규화한다.

    Dreaming 산출물(memory_items / insights)과 사용자의 organic 질문(user_messages)
    에서 topic hint 를 만들고, 자동 산출물(auto_reports)은 낮은 가중치로만 반영한다.
    결과는 가중치 내림차순으로 정렬되어, 관심도가 높은 신호가 앞에 온다.

    Args:
        user_messages: 최근 사용자 메시지(문자열 또는 ``{"text": ...}``).
        memory_items: memory_items read model(dict 또는 :class:`MemoryItem`).
        insights: insights.jsonl 항목(dict 또는 :class:`InsightMeta`).
        auto_reports: cron/recipe 자동 산출물(문자열 또는 ``{"text": ...}``).

    Returns:
        가중치 내림차순으로 정렬된 :class:`InterestSignal` 리스트.
    """
    signals: list[InterestSignal] = []
    signals.extend(signals_from_user_messages(user_messages))
    signals.extend(signals_from_memory_items(memory_items))
    signals.extend(signals_from_insights(insights))
    signals.extend(signals_from_auto_reports(auto_reports))

    # 빈 topic_hint 신호는 후보로 쓸 수 없으므로 제외한다.
    signals = [s for s in signals if s.topic_hint]

    # 가중치 내림차순 정렬(동률이면 confidence, 그래도 같으면 원문으로 안정 정렬).
    signals.sort(key=lambda s: (-s.weight, -s.confidence, s.text))
    return signals


# --------------------------------------------------------------------------- #
# 형 변환 유틸
# --------------------------------------------------------------------------- #


def _coerce_float(value: Any) -> float | None:
    """float 로 변환 가능하면 변환, 아니면 ``None``."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_timestamp(value: Any) -> str | None:
    """타임스탬프를 ISO 문자열로 정규화한다(datetime 이면 isoformat)."""
    if value is None:
        return None
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        try:
            return iso()
        except Exception:  # noqa: BLE001 — 비정상 datetime 이면 문자열 폴백
            return str(value)
    return str(value)
