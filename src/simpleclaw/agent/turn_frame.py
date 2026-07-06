"""Follow-up/축약 사용자 발화의 맥락 정규화(TurnFrame) 모듈.

BIZ-425 — `그럼 현재 순위는?` 같은 follow-up 질문은 원문만으로는 대상이
애매해서 route classifier 가 과승격(complex)하거나 자산 선택이 빗나갈 수 있다.
이 모듈은 사용자 원문(`original_text`)을 절대 덮어쓰지 않고, 최근 대화 맥락의
핵심 키워드를 붙인 내부 라우팅용 질문(`normalized_question`)을 별도로 만든다.

설계 결정:
- LLM 을 쓰지 않는 cheap heuristic 만 사용한다 — 모든 turn 에서 실행되므로
  레이턴시/비용을 더하지 않아야 한다. LLM resolver 는 후속 이슈에서 선택 도입.
- 도메인별 분기(스포츠/주식 등)를 두지 않는다. 최근 메시지에서 일반적인
  키워드 추출로 맥락 후보를 만들고, 도메인 판단은 capability metadata 가 맡는다.
- 복원이 확실하지 않으면(복수 맥락 후보 + 지시대명사형 follow-up) 임의로
  고르지 않고 `ambiguity_options` 를 채워 clarify 경로로 넘긴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# follow-up/이어가기 신호 — 이 표현이 있으면 직전 맥락 복원을 시도한다.
_FOLLOWUP_CUES = (
    "그럼",
    "그러면",
    "그거",
    "저거",
    "그것",
    "아까",
    "이어서",
    "마저",
    "그건",
    "이건",
    "다시",
)
# 지시대명사형 신호 — 자체 주제어 없이 직전 대상을 가리키는 표현.
# 복수 맥락 후보가 있을 때 이 신호가 있으면 임의 선택 대신 clarify 한다.
_REFERENTIAL_CUES = ("그거", "저거", "그것", "아까", "다시", "확인")

# 키워드 추출용 토큰 패턴 — 한글/영문/숫자 2자 이상.
_TOKEN_RE = re.compile(r"[A-Za-z0-9가-힣][A-Za-z0-9가-힣_\-]+")

# 조사/어미 — 토큰 후미에서 잘라 내용어(명사류)만 남기기 위한 근사치.
# 형태소 분석기 없이 동작해야 하므로 빈도 높은 것만 길이순으로 시도한다.
_PARTICLE_SUFFIXES = (
    "에서는",
    "이라고",
    "했습니다",
    "합니다",
    "입니다",
    "있습니다",
    "됐습니다",
    "되었습니다",
    "에서",
    "에게",
    "께서",
    "라고",
    "까지",
    "부터",
    "처럼",
    "보다",
    "으로",
    "했어",
    "됐어",
    "던데",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "에",
    "와",
    "과",
    "도",
    "의",
    "만",
    "로",
    "요",
)
# 내용어가 아닌 기능어/의문사/시제어 — 맥락 키워드에서 제외.
_STOPWORDS = frozenset(
    {
        "오늘",
        "내일",
        "어제",
        "현재",
        "지금",
        "어떻게",
        "어때",
        "무엇",
        "뭐야",
        "어디",
        "언제",
        "누구",
        "왜",
        "그리고",
        "그래서",
        "하지만",
        "있었",
        "있어",
        "없어",
        "됐지",
        "되었",
        "되었지",
        "해줘",
        "주세요",
        "알려줘",
        "보여줘",
        "확인해줘",
        "확인",
        "질문",
        "대답",
        "답변",
    }
)

# follow-up 으로 간주할 발화 최대 길이 — cue 가 없어도 이 이하면 축약으로 본다.
_SHORT_FOLLOWUP_MAX_CHARS = 10
# 맥락 후보로 유지할 최근 user 발화 수 상한.
_MAX_CANDIDATES = 3
# normalized_question 접두에 붙일 맥락 키워드 수 상한.
_MAX_CONTEXT_KEYWORDS = 4


@dataclass(frozen=True)
class ContextCandidate:
    """최근 대화에서 추출한 단일 맥락 후보."""

    summary: str
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class TurnFrame:
    """원문 발화와 내부 라우팅용 정규화 질문을 분리해 담는 turn 프레임.

    `original_text` 는 DB 저장/감사용으로 그대로 보존되고,
    `normalized_question` 만 route/capability/asset 판단에 쓰인다.
    """

    original_text: str
    normalized_question: str
    context_summary: str = ""
    confidence: float = 1.0
    ambiguity_options: list[str] = field(default_factory=list)

    @property
    def needs_clarification(self) -> bool:
        """확신이 낮거나 맥락 후보가 복수면 사용자에게 되물어야 하는지."""
        return self.confidence < 0.65 or len(self.ambiguity_options) >= 2


def _strip_particle(token: str) -> str:
    """토큰 후미의 조사/어미를 잘라 내용어 근사치를 만든다."""
    for suffix in _PARTICLE_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 2:
            return token[: -len(suffix)]
    return token


def _extract_keywords(text: str) -> list[str]:
    """자유 텍스트에서 맥락 키워드(내용어 근사치)를 등장 순서대로 뽑는다."""
    keywords: list[str] = []
    for match in _TOKEN_RE.finditer(text or ""):
        token = _strip_particle(match.group(0))
        if len(token) < 2 or token in _STOPWORDS:
            continue
        if token not in keywords:
            keywords.append(token)
    return keywords


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    lowered = (text or "").lower()
    return any(needle.lower() in lowered for needle in needles)


def extract_context_candidates(
    recent_messages: list[dict] | None,
) -> list[ContextCandidate]:
    """최근 대화를 user 발화 단위 맥락 후보로 묶는다(최신 순).

    user 발화 하나 + 뒤따르는 assistant 응답을 한 후보로 보고, 키워드가
    겹치는 후보는 같은 주제로 병합한다. 도메인 지식 없이 키워드 집합만으로
    후보를 구분하므로 어떤 주제 조합에도 동일하게 동작한다.
    """
    if not recent_messages:
        return []

    # user 발화를 anchor 로 삼아 (user, 이후 assistant 들) 묶음을 만든다.
    exchanges: list[list[str]] = []
    for message in recent_messages:
        role = str(message.get("role", ""))
        content = str(message.get("content", ""))
        if role == "user":
            exchanges.append([content])
        elif role == "assistant" and exchanges:
            exchanges[-1].append(content)

    candidates: list[ContextCandidate] = []
    for exchange in reversed(exchanges):  # 최신 후보 먼저
        keywords = _extract_keywords("\n".join(exchange))
        if not keywords:
            continue
        keyword_set = set(keywords)
        # 이미 수집된(더 최신) 후보와 키워드가 겹치면 같은 주제로 병합한다.
        merged = False
        for idx, existing in enumerate(candidates):
            if keyword_set & set(existing.keywords):
                combined = list(existing.keywords)
                combined.extend(k for k in keywords if k not in combined)
                candidates[idx] = ContextCandidate(
                    summary=_summarize_keywords(combined),
                    keywords=tuple(combined),
                )
                merged = True
                break
        if not merged:
            candidates.append(
                ContextCandidate(
                    summary=_summarize_keywords(keywords),
                    keywords=tuple(keywords),
                )
            )
        if len(candidates) >= _MAX_CANDIDATES:
            break
    return candidates


def _summarize_keywords(keywords: list[str]) -> str:
    """맥락 후보를 사용자에게 보여줄 짧은 한국어 라벨로 만든다."""
    head = ", ".join(keywords[:3])
    return f"'{head}' 관련 직전 맥락"


def _is_followup(text: str) -> bool:
    """발화가 직전 맥락에 기대는 follow-up/축약 표현인지 판정한다."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    if _contains_any(stripped, _FOLLOWUP_CUES):
        return True
    return len(stripped) <= _SHORT_FOLLOWUP_MAX_CHARS


def build_turn_frame(
    text: str,
    *,
    recent_messages: list[dict] | None = None,
) -> TurnFrame:
    """발화 하나를 TurnFrame 으로 변환한다.

    판정 순서:
    1. follow-up 이 아니거나 복원할 맥락이 없으면 원문 그대로(고신뢰).
    2. follow-up 의 자체 키워드가 특정 후보와 겹치면 그 후보로 복원.
    3. 후보가 하나뿐이면 그 후보로 복원.
    4. 후보가 복수 + 지시대명사형이면 ambiguity_options 를 채워 clarify 유도.
    5. 그 외에는 가장 최신 후보로 복원(중간 신뢰) — 과잉 되묻기를 피한다.
    """
    original = text or ""
    candidates = extract_context_candidates(recent_messages)

    if not _is_followup(original) or not candidates:
        return TurnFrame(
            original_text=original,
            normalized_question=original,
            confidence=0.95,
        )

    own_keywords = set(_extract_keywords(original))
    overlapping = [c for c in candidates if own_keywords & set(c.keywords)]
    if overlapping:
        # 자체 주제어가 특정 맥락과 이어짐 — 가장 최신 겹침 후보로 복원한다.
        chosen = overlapping[0]
        confidence = 0.8 if len(overlapping) == 1 else 0.7
        return _normalized_frame(original, chosen, confidence)

    if len(candidates) == 1:
        return _normalized_frame(original, candidates[0], 0.75)

    if _contains_any(original, _REFERENTIAL_CUES):
        # 복수 맥락 + 대상 불명 지시어 — 임의 선택 대신 사용자에게 묻는다.
        return TurnFrame(
            original_text=original,
            normalized_question=original,
            context_summary="최근 대화에 복수 맥락 후보가 있습니다.",
            confidence=0.45,
            ambiguity_options=[c.summary for c in candidates],
        )

    # 복수 후보지만 지시어가 없으면 가장 최신 맥락을 채택한다(되묻기 최소화).
    return _normalized_frame(original, candidates[0], 0.7)


def _normalized_frame(
    original: str, candidate: ContextCandidate, confidence: float
) -> TurnFrame:
    """선택된 맥락 후보의 키워드를 접두어로 붙인 정규화 프레임을 만든다.

    접두 문구는 route cue 단어(기준/조건/규칙/비교 등)를 피해서 작성한다 —
    정규화 자체가 complex 승격 점수를 인위적으로 올리면 안 되기 때문.
    """
    context_keywords = ", ".join(candidate.keywords[:_MAX_CONTEXT_KEYWORDS])
    normalized = f"(직전 대화 맥락: {context_keywords}) {original}"
    return TurnFrame(
        original_text=original,
        normalized_question=normalized,
        context_summary=candidate.summary,
        confidence=confidence,
    )
