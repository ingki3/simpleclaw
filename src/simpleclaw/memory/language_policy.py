"""BIZ-80 — Dreaming 산출물의 1차 언어 정규화.

dreaming 이 USER.md / MEMORY.md / AGENT.md / SOUL.md 의 ``managed:dreaming:*``
섹션에 쓰는 모든 본문은 사용자가 정한 *1차 언어* 와 일치해야 한다. 영어 입력
대화에서도 USER.md 인사이트는 한국어로 적힌다 — 이렇게 해야 retrieval 임베딩이
한 언어 기준으로 일관되고, 사람이 읽는 가독성도 흐트러지지 않는다.

본 모듈은 두 가지를 제공한다:

1. ``LanguagePolicy`` — 파일별 1차 언어 설정.
2. 검출 헬퍼 — bullet/문자열/메타 항목이 1차 언어와 일치하는지 판정하고,
   불일치 항목을 *드롭* 한다(자동 번역 대신 reject 경로 채택; 부모 BIZ-66 §3-J 의
   "자동 번역 또는 거절" 중 결정성·재현성이 더 좋은 거절 쪽). 운영자는
   ``scripts/migrate_dreaming_language.py`` 로 1회성 LLM 번역을 실행할 수 있다.

검출은 휴리스틱이다 — 짧은 한국어 문장에서도 BIZ-XX 같은 ASCII 토큰은 자연스럽게
섞인다. 한 항목 안의 *알파벳/한자/한글 모음 중 한글의 비율* 이 임계치 (기본 0.3)
이상이면 한국어로 본다. 너무 빡빡하게 잡으면 "BIZ-66 진행" 같은 멀쩡한 한국어
bullet 이 잘려 나가고, 너무 느슨하면 영어 요약문이 그대로 살아남는다 — 0.3 은
실제 dreaming 산출물 (test_dreaming_language.py 픽스처) 에서 두 모드를 모두
잘 갈라 내는 값으로 잡았다.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable

logger = logging.getLogger(__name__)


# 지원 언어 — 현재는 한국어/영어만 의미 있는 출력 대상이다. 다른 언어는 "primary"
# 로 지정해도 검출 로직이 동작하지만, 휴리스틱 신뢰도는 낮을 수 있다.
LANG_KOREAN = "ko"
LANG_ENGLISH = "en"

_SUPPORTED_LANGS = frozenset({LANG_KOREAN, LANG_ENGLISH})

# 한국어 문자(한글 음절 + 자모) 범위. 정규식 ``\uAC00-\uD7A3`` 가 표준 음절,
# ``\u3131-\u318E`` 가 호환 자모. 중국 한자(漢字) 는 한국어로 보지 않는다 —
# 일본어 하이쿠나 중국어 인용을 한국어로 오인하지 않게.
_HANGUL_RE = re.compile(r"[\uAC00-\uD7A3\u3131-\u318E\uA960-\uA97C\uD7B0-\uD7FB]")
# ASCII 라틴 알파벳. 한국어 본문에 자주 섞이는 BIZ-XX, USER.md, GitHub 같은 토큰을
# "라틴 비중" 으로 헤아린다.
_LATIN_RE = re.compile(r"[A-Za-z]")
# Bullet 접두 ("- ", "* ", "+ ") 와 leading whitespace 제거용.
_BULLET_PREFIX_RE = re.compile(r"^\s*[-*+]\s+")
# 마크다운 자유 헤더 (``## 2026-04-28`` 같은) — 헤더 자체는 1차 언어 검사에서
# 제외한다(날짜/숫자/영문 라벨이 들어가는 것이 자연스럽다).
_HEADER_LINE_RE = re.compile(r"^\s*#{1,6}\s")


@dataclass(frozen=True)
class LanguagePolicy:
    """파일별 dreaming 출력의 1차 언어 정책 (BIZ-80).

    ``primary`` 가 None 이면 검사를 끄고 모든 출력을 통과시킨다(레거시 호환).
    ``min_ratio`` 는 한국어 검출 임계치 — 한 항목 안의 ``hangul / (hangul + latin)``
    비율이 이 값 이상이어야 1차 언어로 본다. 0.0 으로 설정하면 어떤 라틴
    토큰이 끼어 있어도 항상 통과(검사 비활성과 사실상 동일).

    파일별 다른 언어를 쓰고 싶다면 ``per_file`` 에 파일 식별자(``"user"``,
    ``"memory"``, ``"agent"``, ``"soul"``) → 언어 코드 매핑을 넣을 수 있다.
    BIZ-80 DoD 에 따르면 USER/MEMORY/AGENT/SOUL 모두 ``ko`` 로 두는 것이 표준.
    """

    primary: str | None = LANG_KOREAN
    min_ratio: float = 0.3
    per_file: dict[str, str] = field(default_factory=dict)
    # ``True`` 면 비-1차 언어 항목을 만나도 fail-closed 로 abort 하지 않고 silently
    # drop 한다. 운영 안정성 우선(부분 dreaming 결과는 0건보다 가치가 있다)이므로
    # 기본 True. 회귀 테스트가 켤 때만 strict 검증.
    drop_on_violation: bool = True

    def language_for(self, file_kind: str | None) -> str | None:
        """``file_kind`` (``"user"`` | ``"memory"`` | ...) 의 1차 언어를 반환한다.

        매핑이 없으면 ``primary`` 폴백, ``primary`` 도 없으면 None.
        """
        if file_kind and file_kind in self.per_file:
            return self.per_file[file_kind]
        return self.primary


def is_primary_language(text: str, lang: str | None, *, min_ratio: float = 0.3) -> bool:
    """``text`` 가 ``lang`` 1차 언어로 작성된 것으로 보이는지 휴리스틱 판정.

    - ``lang`` 이 None 이면 검사를 끄고 항상 True (정책 비활성).
    - ``text`` 가 공백/숫자/마크다운 메타뿐(알파벳도 한글도 없음) 이면 1차 언어로
      간주(비어있는 토큰을 끊을 이유는 없다 — 예: ``- 2026-04-28`` 같은 날짜 bullet).
    - 한국어: ``hangul / (hangul + latin)`` 비율이 ``min_ratio`` 이상이면 True.
    - 영어: latin 비율이 ``min_ratio`` 이상이고 한글이 거의 없으면 True.
    - 그 외 lang: latin/hangul 어느 쪽도 강하지 않을 때 True (보수적 통과).

    Args:
        text: 검사할 본문 (한 줄 또는 여러 줄). bullet 접두/마크다운 헤더는 호출자가
            잘라 두지 않아도 무방 — 본 함수는 letter 카운팅 기반이라 마크다운 기호의
            영향을 거의 받지 않는다.
        lang: 1차 언어 코드. ``None`` 또는 빈 문자열이면 항상 True.
        min_ratio: 1차 언어 비중 임계치 (0.0 ~ 1.0).
    """
    if not lang:
        return True
    s = unicodedata.normalize("NFC", text or "")
    hangul = len(_HANGUL_RE.findall(s))
    latin = len(_LATIN_RE.findall(s))
    if hangul + latin == 0:
        # 알파벳도 한글도 없는 항목 — 날짜/숫자/순수 기호. "위반 아님" 으로 통과.
        return True
    if lang == LANG_KOREAN:
        return hangul / (hangul + latin) >= min_ratio
    if lang == LANG_ENGLISH:
        # 한국어가 강하면 영어로 보지 않는다(혼용도 한국어 지배 → 영어 reject).
        return hangul == 0 or latin / (hangul + latin) >= max(min_ratio, 0.7)
    # 알 수 없는 언어 — 휴리스틱으로 단정하지 않고 통과시킨다.
    return True


def split_bullets(text: str) -> list[tuple[str, str]]:
    """마크다운 bullet 텍스트를 ``(indent_prefix, body)`` 튜플 목록으로 분해.

    - 빈 줄 / 헤더 / 일반 텍스트 줄도 그대로 포함된다(``prefix`` 는 빈 문자열).
    - bullet 줄은 ``prefix = "- "`` 형태로 분리되어 ``body`` 만 검사 대상이 된다.

    이 분리는 ``filter_text_to_primary`` 가 *bullet 단위* 로만 reject 결정을
    내릴 수 있게 한다 — 한 줄 한 줄을 따로 평가해서 한국어 bullet 사이에 끼어
    있는 영어 bullet 만 골라 떨궈 낸다.
    """
    out: list[tuple[str, str]] = []
    for line in (text or "").splitlines():
        m = _BULLET_PREFIX_RE.match(line)
        if m:
            out.append((m.group(0), line[m.end():]))
        else:
            out.append(("", line))
    return out


def filter_text_to_primary(
    text: str,
    lang: str | None,
    *,
    min_ratio: float = 0.3,
) -> tuple[str, list[str]]:
    """``text`` 의 bullet 줄 중 1차 언어가 아닌 줄을 드롭한다.

    헤더 줄(``## ...``) 과 빈 줄, 일반 텍스트(bullet 이 아닌 줄) 는 검사 없이 통과 —
    날짜 헤더 ``## 2026-04-28`` 이나 번역 가능한 배경 설명을 굳이 자르지 않는다.

    Returns:
        ``(filtered_text, dropped_bullets)`` — ``dropped_bullets`` 는 reject 된
        본문 텍스트(``"- "`` 접두 없이) 의 목록. 운영 진단에 활용한다.
    """
    if not lang or not text:
        return text or "", []
    kept_lines: list[str] = []
    dropped: list[str] = []
    for prefix, body in split_bullets(text):
        if not prefix:
            # bullet 이 아닌 줄(헤더/빈 줄/일반 텍스트) 은 보존.
            kept_lines.append(body)
            continue
        if _HEADER_LINE_RE.match(body):
            # 본문이 헤더로 시작하는 비정상 형태 — 그대로 둔다.
            kept_lines.append(prefix + body)
            continue
        if is_primary_language(body, lang, min_ratio=min_ratio):
            kept_lines.append(prefix + body)
        else:
            dropped.append(body.strip())
    return "\n".join(kept_lines), dropped


def filter_meta_items(
    items: Iterable[dict],
    lang: str | None,
    *,
    min_ratio: float = 0.3,
) -> tuple[list[dict], list[dict]]:
    """``user_insights_meta`` 항목 중 1차 언어가 아닌 것을 드롭.

    - ``topic`` 과 ``text`` 둘 다 검사. 둘 중 하나라도 비-1차 언어면 reject.
      topic 이 정규형 키 역할을 하므로 (``"맥북에어가격"``) 한국어 문자열로
      적혀 있어야 sidecar 매칭이 일관된다.
    - 다른 키(``source_msg_ids`` 등)는 보존.

    Returns:
        ``(kept, dropped)`` — ``dropped`` 는 reject 된 원본 dict 의 *얕은 복사*.
        호출자가 진단 로그/Admin 알림에 사용 가능.
    """
    if not lang:
        return list(items), []
    kept: list[dict] = []
    dropped: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        topic = (item.get("topic") or "").strip()
        text = (item.get("text") or "").strip()
        if not topic or not text:
            # 빈 항목은 BIZ-73 검증에서 별도로 잘려 나간다 — 여기선 통과.
            kept.append(item)
            continue
        if is_primary_language(topic, lang, min_ratio=min_ratio) and is_primary_language(
            text, lang, min_ratio=min_ratio
        ):
            kept.append(item)
        else:
            dropped.append(dict(item))
    return kept, dropped


def filter_active_projects(
    projects: Iterable[dict],
    lang: str | None,
    *,
    min_ratio: float = 0.3,
) -> tuple[list[dict], list[dict]]:
    """Active project 관측치 중 1차 언어가 아닌 것을 드롭.

    프로젝트 이름은 종종 고유명사(영문 ``"SimpleClaw"``, ``"Multica"``) 라서
    ``name`` 자체는 검사하지 않는다. 검사 대상은 ``role`` / ``recent_summary``
    의 합본 — 두 필드가 모두 비어 있으면 통과(빈 항목은 별도 단계에서 처리).
    한 필드라도 비-1차 언어 비중이 높으면 reject.

    Returns:
        ``(kept, dropped)`` — ``dropped`` 는 reject 된 원본 dict.
    """
    if not lang:
        return list(projects), []
    kept: list[dict] = []
    dropped: list[dict] = []
    for proj in projects:
        if not isinstance(proj, dict):
            continue
        role = str(proj.get("role") or "").strip()
        summary = str(proj.get("recent_summary") or "").strip()
        text = " ".join(filter(None, [role, summary]))
        if not text:
            kept.append(proj)
            continue
        if is_primary_language(text, lang, min_ratio=min_ratio):
            kept.append(proj)
        else:
            dropped.append(dict(proj))
    return kept, dropped


def language_instruction_block(policy: LanguagePolicy) -> str:
    """LLM dreaming 프롬프트에 삽입할 1차 언어 강제 지시문 (BIZ-80).

    파일별 ``per_file`` 매핑이 비어 있으면 단일 언어 지시만, 매핑이 있으면
    파일별로 따로 명시한다. ``primary`` 가 None 이면 빈 문자열을 반환 —
    프롬프트에 어떤 언어 강제도 들어가지 않는다(레거시 호환).
    """
    if not policy.primary:
        return ""

    name = _human_lang_name(policy.primary)
    base = (
        f"⚠️ 출력 언어 규칙(BIZ-80, 매우 중요): 응답에 포함되는 모든 본문 — "
        f"`memory`, `user_insights`, `user_insights_meta` 의 `topic` 과 `text`, "
        f"`soul_updates`, `agent_updates`, `active_projects` 의 `role` 과 "
        f"`recent_summary` — 은 **{name}** 로 작성하세요. 사용자가 영어로 입력해도, "
        f"예를 들어 `\"I want a daily plan\"` 같은 메시지를 받아도, "
        f"산출물은 {name} 로 번역해서 적습니다. 고유명사(예: SimpleClaw, BIZ-XX) 는 "
        f"원어 그대로 유지하되 그 외 일반 명사·동사·서술어는 모두 {name} 입니다. "
        f"비-{name} 본문은 시스템이 자동으로 거부합니다 — 거부된 항목은 반영되지 않습니다."
    )
    overrides = []
    if policy.per_file:
        for kind, lang in sorted(policy.per_file.items()):
            if lang == policy.primary:
                continue
            overrides.append(f"- {kind.upper()}: {_human_lang_name(lang)}")
    if overrides:
        base += "\n파일별 1차 언어 오버라이드:\n" + "\n".join(overrides)
    return base


def _human_lang_name(code: str) -> str:
    """언어 코드를 프롬프트용 사람이 읽는 이름으로."""
    if code == LANG_KOREAN:
        return "한국어"
    if code == LANG_ENGLISH:
        return "English"
    return code


def is_supported(lang: str | None) -> bool:
    """``lang`` 이 본 모듈의 휴리스틱이 신뢰할 만한 코드인지."""
    return lang in _SUPPORTED_LANGS
