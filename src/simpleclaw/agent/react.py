"""ReAct (Reasoning + Acting) 엔진 — LLM 응답 파서 모듈.

LLM의 ReAct 형식 응답을 Thought, Action, Answer 세 요소로 파싱한다.

동작 흐름:
1. 응답에서 "Thought:" 블록을 추출 (추론 과정)
2. "Answer:" 블록이 있으면 최종 답변으로 반환
3. "Action:" 블록이 있으면 JSON을 파싱하여 도구 호출 정보로 반환

설계 결정:
- JSON 추출 시 정규식 대신 중괄호 깊이 카운팅 사용 — 중첩 객체 처리 정확성
- 문자열 내부의 중괄호/이스케이프를 올바르게 처리
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


def _extract_json_object(text: str) -> str | None:
    """텍스트에서 첫 번째 최상위 JSON 객체를 중괄호 깊이 카운팅으로 추출한다.

    비탐욕적 정규식과 달리 중첩 객체를 정확히 처리한다.
    """
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]

    return None


def parse_react(
    response: str,
) -> tuple[str | None, dict | None, str | None]:
    """ReAct 응답을 (thought, action, answer) 튜플로 파싱한다.

    Returns:
        thought: Thought 텍스트 (없으면 None).
        action: 파싱된 Action JSON 딕셔너리 (없으면 None).
        answer: Answer 텍스트 (없으면 None).

    Answer가 있으면 action은 항상 None이 된다 (최종 답변 우선).
    """
    thought = None
    action = None
    answer = None

    # Thought 추출 — 행 앵커로 오탐 방지
    thought_match = re.search(
        r"^Thought:\s*(.+?)(?=\nAction:|\nAnswer:|\Z)",
        response, re.DOTALL | re.MULTILINE,
    )
    if thought_match:
        thought = thought_match.group(1).strip()

    # Answer 추출 — 행 앵커 사용
    answer_match = re.search(
        r"^Answer:\s*(.+)", response, re.DOTALL | re.MULTILINE
    )
    if answer_match:
        answer = answer_match.group(1).strip()
        return thought, None, answer

    # Action 추출 — 중첩 JSON을 위한 중괄호 깊이 카운팅
    action_match = re.search(
        r"^Action:\s*", response, re.MULTILINE
    )
    if action_match:
        json_text = _extract_json_object(response[action_match.end():])
        if json_text:
            try:
                action = json.loads(json_text)
            except json.JSONDecodeError:
                logger.warning(
                    "Failed to parse Action JSON: %s",
                    json_text[:200],
                )

    return thought, action, answer
