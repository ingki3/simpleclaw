"""드리밍이 갱신할 수 있는 영역(Protected Section)을 식별·강제하는 모듈.

배경:
    AGENT.md가 수십 줄에서 두 줄로 축소된 사고(BIZ-66 §1)는 드리밍 파이프라인에
    "어떤 영역이 외부 소유라 손대면 안 되는가"라는 개념이 없다는 것을 드러냈다.
    본 모듈은 드리밍이 합법적으로 쓸 수 있는 영역을 마커로 식별하고, 그 외
    영역에 대한 모든 쓰기를 차단(fail-closed)하기 위한 1차적 토대다.

마커 형식:
    `<!-- managed:dreaming:<section> -->` ... `<!-- /managed:dreaming:<section> -->`

    - `<section>`은 `[A-Za-z0-9_-]+` 식별자(예: `insights`, `journal`, `clusters`).
    - 한 파일에 여러 managed 섹션이 있을 수 있으나 같은 이름이 두 번 나오면 안 된다.
    - 마커는 중첩 불가(시작이 닫히기 전에 다른 시작이 나오면 malformed).
    - 마커 외부의 모든 텍스트는 드리밍 입장에서 read-only다 — 본 모듈의 어떤
      함수도 마커 외부 바이트를 변경하지 않는다.

설계 결정:
    - 본 모듈은 "쓸 수 있는 위치"만 정한다. 드리밍이 그 위치에 무엇을 쓰는지는
      `dreaming.py`가 정한다(예: `## Dreaming Insights (YYYY-MM-DD)` 헤더를
      append).
    - "마커 누락" 상황은 침묵하지 않는다 — `ProtectedSectionMissing`을 던져 호출자가
      전체 사이클을 중단하도록 한다(fail-closed). 자동 마이그레이션·자동 마커 삽입은
      본 모듈의 역할이 아니다(별도 부트스트랩 단계의 책임).
    - 마커 본문은 줄바꿈으로 정규화되어 쓰여진다(시작 마커 직후 `\n`, 끝 마커 직전
      `\n`). 입력에 줄바꿈이 누락돼 있어도 출력은 항상 같은 형태가 된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# 섹션 이름은 영문/숫자/언더스코어/하이픈만 허용해 마커 형식 모호성을 제거한다.
_NAME_RE = r"[A-Za-z0-9_-]+"
_START_RE = re.compile(rf"<!--\s*managed:dreaming:({_NAME_RE})\s*-->")
_END_RE = re.compile(rf"<!--\s*/managed:dreaming:({_NAME_RE})\s*-->")


def _is_marker_on_own_line(text: str, start: int, end: int) -> bool:
    """``text[start:end]`` 가 자기 줄을 단독으로 차지하는지 검사.

    "단독으로 차지" 는 마커 앞쪽이 (파일 시작 또는 줄바꿈) + 임의 공백, 마커 뒤쪽이
    임의 공백 + (줄바꿈 또는 파일 끝) 임을 의미한다. 예를 들어 ``1. <!-- ... -->`` 처럼
    같은 줄의 앞에 prose 가 있으면 *문서 설명용 인라인 mention* 으로 보고 진짜 marker 가
    아닌 것으로 판단한다.
    """
    # 앞쪽: 줄 시작까지 거슬러 올라가 모두 공백이어야 함.
    i = start
    while i > 0 and text[i - 1] in " \t":
        i -= 1
    if i > 0 and text[i - 1] != "\n":
        return False
    # 뒤쪽: 줄 끝까지 모두 공백이어야 함.
    j = end
    n = len(text)
    while j < n and text[j] in " \t":
        j += 1
    if j < n and text[j] != "\n":
        return False
    return True


class ProtectedSectionError(Exception):
    """모든 protected-section 위반의 베이스 예외."""


class ProtectedSectionMissing(ProtectedSectionError):
    """요청된 managed 섹션이 파일에 존재하지 않을 때 발생.

    호출자는 이 예외를 잡아 "fail-closed"(전체 사이클 중단, 파일 보존)로
    응답해야 한다.
    """


class ProtectedSectionMalformed(ProtectedSectionError):
    """마커 자체가 잘못된 경우(중첩, 짝짝이, 같은 이름 중복 등) 발생."""


@dataclass(frozen=True)
class ManagedSection:
    """파싱된 managed 섹션 한 개의 위치 정보.

    Attributes:
        name: 섹션 식별자(예: ``insights``).
        start_marker_offset: 시작 마커 ``<!--``의 첫 문자 오프셋.
        body_offset: 시작 마커 직후 위치(``-->``의 다음 문자). 이 위치 이후가 본문.
        body_end_offset: 끝 마커 ``<!--``의 첫 문자 오프셋. 이 위치 직전까지가 본문.
        end_marker_end_offset: 끝 마커 ``-->``의 다음 문자 오프셋.

    본문 문자열은 ``text[body_offset:body_end_offset]``로 추출된다.
    이 표현은 마커 자체와 본문을 깔끔히 분리해 마커 외부 바이트의 byte-for-byte
    보존을 자명하게 만든다.
    """

    name: str
    start_marker_offset: int
    body_offset: int
    body_end_offset: int
    end_marker_end_offset: int


def find_managed_sections(text: str) -> list[ManagedSection]:
    """텍스트에서 모든 managed 섹션을 파싱하고 짝·중첩·중복을 검증한다.

    Args:
        text: 검사 대상 파일 본문.

    Returns:
        파일 등장 순서로 정렬된 ``ManagedSection`` 리스트. 마커가 하나도 없으면 빈 리스트.

    Raises:
        ProtectedSectionMalformed: 마커가 중첩되거나, 시작/끝 이름이 일치하지 않거나,
            짝이 없거나, 같은 이름이 두 번 이상 시작될 때.
    """
    # 시작·끝 마커를 모두 모아 등장 순으로 정렬 후 스택으로 짝 검증.
    # 이 방식은 정규식 한 번으로 시작/끝을 동시에 잡으려는 시도보다 훨씬 견고하다 —
    # 시작과 끝이 다른 줄에 있어도, 같은 줄에 있어도 동일하게 동작한다.
    #
    # 단, marker 는 *자기 줄을 단독으로* 차지하는 형태만 진짜 marker 로 본다.
    # `.agent/MEMORY.md` 등 운영 파일은 최상단 doc 주석 안에 marker 토큰을 *문서
    # 설명용*으로 그대로 적는 경우가 있고("2. <!-- managed:dreaming:journal --> ~ ..."),
    # 그 인라인 등장은 진짜 marker 가 아니다. 줄 단독 제약(앞뒤가 줄 경계 + 공백뿐)을
    # 추가해 doc 안 인라인 mention 이 fail-closed 트랩이 되지 않도록 한다 (BIZ-104).
    tokens: list[tuple[str, int, int, str]] = []
    for m in _START_RE.finditer(text):
        if _is_marker_on_own_line(text, m.start(), m.end()):
            tokens.append(("start", m.start(), m.end(), m.group(1)))
    for m in _END_RE.finditer(text):
        if _is_marker_on_own_line(text, m.start(), m.end()):
            tokens.append(("end", m.start(), m.end(), m.group(1)))
    tokens.sort(key=lambda t: t[1])

    sections: list[ManagedSection] = []
    open_stack: list[tuple[str, int, int]] = []  # (name, start_marker_offset, body_offset)

    for kind, marker_start, marker_end, name in tokens:
        if kind == "start":
            if open_stack:
                # 중첩 금지 — 단일 깊이만 허용. 중첩을 허용하면 본문이 또 다른 본문을 포함하게
                # 되어 "어디까지가 본문인가"가 모호해진다.
                raise ProtectedSectionMalformed(
                    f"managed:dreaming 섹션 중첩 금지: '{open_stack[-1][0]}'이 닫히기 전에 "
                    f"'{name}'이 시작됨 (offset={marker_start})"
                )
            open_stack.append((name, marker_start, marker_end))
        else:  # end
            if not open_stack:
                raise ProtectedSectionMalformed(
                    f"매칭되는 시작 마커 없이 끝 마커 출현: "
                    f"<!-- /managed:dreaming:{name} --> (offset={marker_start})"
                )
            open_name, open_start, open_end = open_stack.pop()
            if open_name != name:
                raise ProtectedSectionMalformed(
                    f"마커 짝 불일치: <!-- managed:dreaming:{open_name} --> 가 "
                    f"<!-- /managed:dreaming:{name} --> 로 닫힘"
                )
            sections.append(
                ManagedSection(
                    name=name,
                    start_marker_offset=open_start,
                    body_offset=open_end,
                    body_end_offset=marker_start,
                    end_marker_end_offset=marker_end,
                )
            )

    if open_stack:
        unclosed = ", ".join(name for name, _, _ in open_stack)
        raise ProtectedSectionMalformed(f"닫히지 않은 managed 섹션: {unclosed}")

    seen: dict[str, int] = {}
    for sec in sections:
        if sec.name in seen:
            raise ProtectedSectionMalformed(
                f"같은 이름의 managed 섹션이 여러 번 정의됨: '{sec.name}' "
                f"(첫 번째 offset={seen[sec.name]}, 두 번째 offset={sec.start_marker_offset})"
            )
        seen[sec.name] = sec.start_marker_offset

    return sections


def get_managed_section(text: str, name: str) -> ManagedSection:
    """이름으로 단일 섹션을 조회한다.

    Raises:
        ProtectedSectionMissing: 해당 이름의 섹션이 없을 때.
        ProtectedSectionMalformed: 마커 자체가 잘못된 경우(``find_managed_sections``에서 전파).
    """
    for sec in find_managed_sections(text):
        if sec.name == name:
            return sec
    raise ProtectedSectionMissing(
        f"managed 섹션을 찾을 수 없음: <!-- managed:dreaming:{name} -->"
    )


def get_section_body(text: str, name: str) -> str:
    """이름으로 섹션 본문 문자열을 조회한다.

    본문은 마커 사이의 모든 문자(앞뒤 줄바꿈 포함)를 그대로 반환한다.
    호출자가 필요하면 ``strip("\\n")`` 등으로 정규화한다.
    """
    sec = get_managed_section(text, name)
    return text[sec.body_offset : sec.body_end_offset]


def replace_section_body(text: str, name: str, new_body: str) -> str:
    """이름의 섹션 본문을 ``new_body``로 교체하고 그 외 영역은 byte-for-byte 보존한다.

    출력 형식은 항상 정규화된다:
        ``...<!-- managed:dreaming:<name> -->\\n<new_body>\\n<!-- /managed:dreaming:<name> -->...``

    ``new_body``의 앞뒤 줄바꿈은 무시된다(중복 줄바꿈 방지). 빈 본문이면 마커 사이에
    한 줄만 남는다.

    Args:
        text: 원본 파일 본문.
        name: 갱신할 섹션 이름.
        new_body: 새 본문 문자열.

    Returns:
        새 파일 본문. 원본 ``text``는 변경되지 않는다.

    Raises:
        ProtectedSectionMissing: 섹션이 없을 때.
        ProtectedSectionMalformed: 마커가 잘못된 경우.
    """
    sec = get_managed_section(text, name)
    body = new_body.strip("\n")
    if body:
        replacement = "\n" + body + "\n"
    else:
        replacement = "\n"
    return text[: sec.body_offset] + replacement + text[sec.body_end_offset :]


def append_to_section(text: str, name: str, content: str) -> str:
    """이름의 섹션 본문 끝에 ``content``를 한 단락 띄워 append한다.

    이미 본문이 있으면 빈 줄 한 개로 구분하여 자연스러운 마크다운 단락을 만든다.
    ``content``가 비어있거나 공백뿐이면 원본을 그대로 반환한다(no-op).

    Args:
        text: 원본 파일 본문.
        name: 갱신할 섹션 이름.
        content: 추가할 마크다운 단락.

    Returns:
        새 파일 본문.

    Raises:
        ProtectedSectionMissing / ProtectedSectionMalformed: 마커 문제.
    """
    chunk = content.strip("\n").strip()
    if not chunk:
        return text

    existing = get_section_body(text, name).strip("\n")
    if existing:
        merged = existing + "\n\n" + chunk
    else:
        merged = chunk
    return replace_section_body(text, name, merged)


def has_managed_section(text: str, name: str) -> bool:
    """파일에 해당 이름의 managed 섹션이 존재하는지 빠르게 확인한다.

    마커가 잘못된 경우(malformed)는 호출자에게 그 사실을 알리도록 예외를 전파한다 —
    silent ``False``는 destructive overwrite의 빌미가 되므로 의도적으로 피한다.
    """
    return any(sec.name == name for sec in find_managed_sections(text))


def build_initial_template(header: str, sections: list[str]) -> str:
    """비어있는 파일에 쓸 1차 템플릿을 생성한다.

    형식:
        # {header}

        <!-- managed:dreaming:section1 -->
        <!-- /managed:dreaming:section1 -->

        <!-- managed:dreaming:section2 -->
        <!-- /managed:dreaming:section2 -->

    이 함수는 본 모듈에서 자동으로 호출되지 않는다. 자동 마커 삽입은 기존 사용자
    콘텐츠를 덮어쓸 위험이 있으므로 명시적 부트스트랩(예: 설치 스크립트, 테스트
    fixture)에서만 호출되어야 한다.
    """
    parts: list[str] = [f"# {header}", ""]
    for name in sections:
        parts.append(f"<!-- managed:dreaming:{name} -->")
        parts.append(f"<!-- /managed:dreaming:{name} -->")
        parts.append("")
    # 마지막 빈 줄 제거 후 trailing newline 한 개 보장
    while parts and parts[-1] == "":
        parts.pop()
    parts.append("")
    return "\n".join(parts)


def ensure_initialized(
    file_path: Path,
    header: str,
    sections: list[str],
) -> bool:
    """파일이 없거나 비어있으면 템플릿으로 초기화한다.

    이미 콘텐츠가 있는 파일은 절대 손대지 않는다 — 마커 추가가 필요하면 운영자가
    수동으로 편집해야 한다(자동 삽입은 사용자 콘텐츠 손상의 1차 원인).

    Args:
        file_path: 대상 파일.
        header: 템플릿 첫 줄의 ``# {header}``.
        sections: 만들 managed 섹션 이름 리스트.

    Returns:
        실제로 파일을 새로 만들었거나 비어있던 파일을 채웠으면 True. 기존 콘텐츠가 있어
        손대지 않았으면 False.
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    if file_path.is_file():
        existing = file_path.read_text(encoding="utf-8")
        if existing.strip():
            return False
    file_path.write_text(build_initial_template(header, sections), encoding="utf-8")
    return True
