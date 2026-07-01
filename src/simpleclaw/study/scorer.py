"""Study topic scoring — 관심도·신선도·중요도·감쇠를 한 점수로 합성한다.

Agent Study Wiki 의 topic registry 는 "어떤 주제를 오늘 공부할지"를 결정해야 한다.
이 판단의 입력은 한 가지가 아니다. 사용자가 직접 보인 관심(user_interest), 같은 주제가
얼마나 반복 언급됐는지(repeated_mentions), 시효가 짧아 자주 갱신해야 하는지
(freshness_need), 세상에서 얼마나 중요한 사건인지(global_importance), 그리고 마지막
신호 이후 시간이 얼마나 흘렀는지(recency_decay) 다.

설계 결정:
- **가중합 + clamp**: 위 다섯 신호를 고정 가중치로 합산하고 0.0~1.0 으로 자른다. 가중치는
  ``DEFAULT_SCORE_WEIGHTS`` 에 모아 두고, 운영/실험 시 :class:`ScoreWeights` 로 통째로
  교체할 수 있게 한다. 점수 공식을 코드 여기저기 흩지 않고 한 함수
  (:func:`compute_topic_score`)에 모은 이유는, topic 승격/감쇠 정책 전체가 이 한 숫자에
  의존하므로 변경 지점을 단일화하기 위해서다.
- **결정적(deterministic)**: 시간 외의 무작위성을 두지 않는다. recency 는 호출자가 넘긴
  ``age_hours`` 로만 계산하므로(:func:`recency_decay_factor`), 테스트가 ``now`` 를 고정해
  재현 가능하다.
- **신호 정규화 헬퍼 분리**: 반복 언급 횟수(정수)와 경과 시간(시간 단위)을 0~1 신호로
  바꾸는 변환은 점수 공식과 별개의 관심사이므로 :func:`normalize_mentions`,
  :func:`recency_decay_factor` 로 분리했다. registry 는 이 헬퍼로 원시 신호를 정규화한 뒤
  :func:`compute_topic_score` 에 넘긴다.
"""

from __future__ import annotations

from dataclasses import dataclass

# topic 점수 가중치의 합은 1.0 이어야 한다(아래 ScoreWeights.validate 가 강제).
# 사용자가 직접 보인 관심을 최우선(0.35)으로, 반복 언급과 신선도 필요를 동급(0.20)으로,
# 세상 중요도(0.15)와 최근성(0.10)을 보조 신호로 둔다. "본 적 있음" 이 아니라 "반복적으로
# 관심을 보였는가" 가 승격을 좌우하도록 user_interest + repeated_mentions 에 무게를 싣는다.
_WEIGHT_USER_INTEREST = 0.35
_WEIGHT_REPEATED_MENTIONS = 0.20
_WEIGHT_FRESHNESS_NEED = 0.20
_WEIGHT_GLOBAL_IMPORTANCE = 0.15
_WEIGHT_RECENCY_DECAY = 0.10


@dataclass(frozen=True)
class ScoreWeights:
    """topic 점수의 신호별 가중치.

    합이 1.0 이 아니면 점수 해석(임계값 0.55/0.70 등)이 무의미해지므로 생성 시
    :meth:`validate` 로 강제한다. 운영에서 가중치를 실험하려면 이 객체를 통째로
    바꿔 :func:`compute_topic_score` 에 넘기면 된다.
    """

    user_interest: float = _WEIGHT_USER_INTEREST
    repeated_mentions: float = _WEIGHT_REPEATED_MENTIONS
    freshness_need: float = _WEIGHT_FRESHNESS_NEED
    global_importance: float = _WEIGHT_GLOBAL_IMPORTANCE
    recency_decay: float = _WEIGHT_RECENCY_DECAY

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """가중치 합이 1.0(부동소수 허용오차 내)인지 검증한다."""
        total = (
            self.user_interest
            + self.repeated_mentions
            + self.freshness_need
            + self.global_importance
            + self.recency_decay
        )
        # 부동소수 오차를 고려해 엄격 동등 대신 근사 비교.
        if abs(total - 1.0) > 1e-9:
            raise ValueError(
                f"ScoreWeights must sum to 1.0, got {total!r}"
            )


# 운영 기본 가중치. compute_topic_score 가 weights 미지정 시 사용한다.
DEFAULT_SCORE_WEIGHTS = ScoreWeights()


def _clamp01(value: float) -> float:
    """값을 0.0~1.0 구간으로 자른다(입력 신호 방어 + 출력 정규화 공용)."""
    return max(0.0, min(1.0, value))


def compute_topic_score(
    *,
    user_interest: float,
    repeated_mentions: float,
    freshness_need: float,
    global_importance: float,
    recency_decay: float,
    weights: ScoreWeights = DEFAULT_SCORE_WEIGHTS,
) -> float:
    """다섯 신호를 가중합해 topic 의 0.0~1.0 점수를 만든다.

    모든 입력은 0.0~1.0 정규화 신호로 가정하되, 호출자 실수를 방어하기 위해 각 신호를
    한 번 더 clamp 한 뒤 가중합한다. 결과도 clamp 하므로 항상 [0.0, 1.0] 이다.

    Args:
        user_interest: 사용자가 직접 보인 관심 강도(0~1).
        repeated_mentions: 반복 언급 신호(0~1). 원시 횟수는
            :func:`normalize_mentions` 로 먼저 정규화한다.
        freshness_need: 시효가 짧아 자주 갱신해야 하는 정도(0~1).
        global_importance: 세상에서의 중요도(0~1).
        recency_decay: 최근성(0~1, 1=방금 신호). 경과 시간은
            :func:`recency_decay_factor` 로 정규화한다.
        weights: 신호별 가중치. 기본은 :data:`DEFAULT_SCORE_WEIGHTS`.

    Returns:
        0.0~1.0 으로 clamp 된 topic 점수.
    """
    raw = (
        weights.user_interest * _clamp01(user_interest)
        + weights.repeated_mentions * _clamp01(repeated_mentions)
        + weights.freshness_need * _clamp01(freshness_need)
        + weights.global_importance * _clamp01(global_importance)
        + weights.recency_decay * _clamp01(recency_decay)
    )
    return _clamp01(raw)


def normalize_mentions(count: int, *, saturation: float = 3.0) -> float:
    """반복 언급 횟수를 0~1 의 포화 곡선으로 정규화한다.

    선형 ``count / N`` 은 한 번 폭증한 주제를 과대평가한다. 대신 포화 곡선
    ``count / (count + saturation)`` 을 써서 초반 언급의 한계효용을 크게, 이후를 점차
    작게 만든다(예: saturation=3 이면 1회=0.25, 3회=0.5, 9회≈0.75). 음수/0 은 0.0.

    Args:
        count: 누적 언급 횟수.
        saturation: 곡선이 0.5 에 도달하는 언급 횟수(클수록 천천히 포화).

    Returns:
        0.0~1.0 의 반복 언급 신호.
    """
    if count <= 0:
        return 0.0
    if saturation <= 0:
        # saturation 이 비정상이면 한 번이라도 언급되면 최대로 본다(방어적 기본).
        return 1.0
    return _clamp01(count / (count + saturation))


def recency_decay_factor(age_hours: float, *, half_life_hours: float = 168.0) -> float:
    """마지막 신호 이후 경과 시간을 0~1 최근성 신호로 변환한다(지수 감쇠).

    ``factor = 0.5 ** (age_hours / half_life_hours)`` — 반감기마다 절반으로 줄어든다.
    기본 반감기는 168h(7일)로, 한 주 동안 신호가 없으면 최근성 기여가 절반이 된다.
    미래 타임스탬프(음수 age)는 1.0 으로 본다.

    Args:
        age_hours: 마지막 신호로부터 경과한 시간(시간 단위).
        half_life_hours: 신호가 절반으로 감쇠하는 시간(>0).

    Returns:
        0.0~1.0 의 최근성 신호(1=방금, 0 에 수렴).
    """
    if age_hours <= 0:
        return 1.0
    if half_life_hours <= 0:
        # 반감기가 비정상이면 즉시 0(과거 신호는 최근성 기여 없음).
        return 0.0
    return _clamp01(0.5 ** (age_hours / half_life_hours))
