"""ReAct `clarify` tool: 다지선다 질문을 채널 인라인 키보드로 렌더하는 브리지.

BIZ-260 / Hermes PR #24199 패턴:
- LLM 이 ``clarify(question, options)`` 도구를 호출하면 ``handle_clarify`` 가
  ``ClarifyRequest`` 객체를 contextvar 로 전달된 ``chat_id`` 키에 저장하고 즉시
  tool result 를 돌려준다.
- 오케스트레이터는 tool 호출 후 pending 레지스트리를 보고 추가 LLM 호출 없이
  tool loop 를 종결한다 — clarify 호출은 그 자체로 "사용자에게 다시 묻기" 의도이므로
  연쇄적인 후속 도구 호출 / LLM 텍스트 응답이 필요 없다.
- 채널 (텔레그램) 은 ``process_message`` 가 끝난 직후 ``pop_pending_clarify(chat_id)``
  로 ``ClarifyRequest`` 를 회수해 인라인 키보드로 렌더한다 — 텍스트 본문은
  질문만 노출, 옵션 본문은 버튼 라벨로.

contextvar 를 쓰는 이유:
- 동일 ``AgentOrchestrator`` 인스턴스가 여러 chat 의 ``process_message`` 를
  동시 처리할 때 chat_id 누설 없이 tool 핸들러까지 운반해야 한다 — trace_id
  와 동일한 패턴.

콜백 페이로드(텔레그램 64 byte 한계):
- 옵션 ID 만 페이로드에 싣고 본문은 채널측 인메모리 캐시에서 조회한다.
- ID 포맷은 ``c:<index>`` (예: ``c:0``, ``c:7``) — 3~4 byte 로 64 byte 한계
  에 여유. 이 모듈에선 인덱스 자체를 그대로 노출하고, 64 byte boundary 검증은
  ``encode_callback_data`` 가 책임진다.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field

# 텔레그램 인라인 키보드는 한 줄당 최대 8개, 옵션이 너무 많으면 사용자 가독성 저하.
# Hermes PR #24199 도 8개 cap. 더 많은 다지선다가 필요하면 LLM 측에서 다단계로
# 쪼개야 한다 — clarify 도구는 "한 번에 결정 가능한" 질문에 한정한다.
MAX_CLARIFY_OPTIONS = 8

# 텔레그램 inline button 라벨 한계 (UTF-16 code unit 기준 ≤ 64) — 명목상 한계이며
# 실제론 BUTTON_TEXT_INVALID 에러를 피하기 위한 보수적 cap 으로 32자 권장.
# 옵션 본문이 더 길면 라벨은 잘라서 보여주고, "전체 본문" 은 캐시에서 별도 조회.
MAX_BUTTON_LABEL_CHARS = 48

# 텔레그램 callback_query 페이로드 한계 — 1~64 byte (UTF-8 기준).
# ``c:<idx>`` 포맷에선 ``c:7`` 이 가장 긴 케이스(3 byte) 이지만, 미래 확장 (예:
# message_id prefix) 대비해 hard cap 으로 검증한다.
MAX_CALLBACK_DATA_BYTES = 64

# 콜백 데이터 namespace prefix — 같은 봇이 다른 종류의 inline keyboard 를
# 추가할 때 충돌 없이 라우팅하기 위함. 현재는 clarify 하나뿐.
_CALLBACK_PREFIX = "c"


@dataclass(frozen=True)
class ClarifyOption:
    """단일 다지선다 옵션.

    Attributes:
        index: 0-based 인덱스. callback_data 의 후미 (``c:<idx>``) 와 일치.
        label: 버튼에 표시할 짧은 라벨. ``MAX_BUTTON_LABEL_CHARS`` 로 자른 값.
        body: 옵션의 전체 본문. LLM 이 다음 turn 에서 사용자가 선택한 옵션
              으로 인식할 텍스트 — 사용자가 버튼을 누르면 이 값이 새 메시지로
              주입되고, 텍스트 응답("1") 의 매칭에도 쓰인다.
    """

    index: int
    label: str
    body: str


@dataclass
class ClarifyRequest:
    """LLM 이 ``clarify`` 도구로 발생시킨 단일 요청.

    채널은 ``question`` 을 메시지 본문으로, ``options`` 를 인라인 키보드로
    렌더한다. 대화 이력 저장용 텍스트는 ``format_user_visible`` 가 조립.
    """

    question: str
    options: list[ClarifyOption] = field(default_factory=list)

    def format_user_visible(self) -> str:
        """대화 이력에 저장할 ``질문 + 번호 옵션`` 텍스트.

        다음 turn 의 LLM 컨텍스트에 옵션이 보존되어 사용자가 텍스트로 "1" /
        "Foo" / 본문 텍스트로 답해도 매칭이 가능하도록 한다. 인라인 키보드만
        쓰면 채널 UI 는 깔끔하지만, 봇 재시작 / 텍스트 응답 경로에서는 옵션을
        잃는다.
        """
        lines = [self.question, ""]
        for opt in self.options:
            lines.append(f"{opt.index + 1}. {opt.body}")
        return "\n".join(lines)


def normalize_options(raw_options: list) -> list[ClarifyOption]:
    """LLM 이 넘긴 raw 옵션 리스트를 ``ClarifyOption`` 으로 정규화한다.

    허용 입력:
    - ``list[str]`` — 본문 그대로 (라벨도 본문 첫 ``MAX_BUTTON_LABEL_CHARS`` 자)
    - ``list[dict]`` with ``label`` / ``body`` 키 — 라벨/본문 분리 지정

    빈 옵션·``MAX_CLARIFY_OPTIONS`` 초과는 ``ValueError`` 로 거부 — LLM 이
    잘못된 입력을 주면 tool result 에 명확한 에러 문자열이 돌아간다.
    """
    if not isinstance(raw_options, list) or not raw_options:
        raise ValueError("'options' must be a non-empty list")
    if len(raw_options) > MAX_CLARIFY_OPTIONS:
        raise ValueError(
            f"'options' must have at most {MAX_CLARIFY_OPTIONS} entries "
            f"(got {len(raw_options)})"
        )

    normalized: list[ClarifyOption] = []
    for idx, raw in enumerate(raw_options):
        if isinstance(raw, str):
            body = raw.strip()
            label = body
        elif isinstance(raw, dict):
            body = str(raw.get("body") or raw.get("label") or "").strip()
            label = str(raw.get("label") or body).strip()
        else:
            raise ValueError(
                f"option[{idx}] must be a string or "
                "{label, body} dict"
            )
        if not body:
            raise ValueError(f"option[{idx}] body is empty")
        if len(label) > MAX_BUTTON_LABEL_CHARS:
            label = label[: MAX_BUTTON_LABEL_CHARS - 1] + "…"
        normalized.append(ClarifyOption(index=idx, label=label, body=body))
    return normalized


def encode_callback_data(option_index: int) -> str:
    """옵션 인덱스를 텔레그램 callback_data 페이로드로 직렬화한다.

    포맷: ``c:<idx>``. ``MAX_CALLBACK_DATA_BYTES`` 를 넘으면 ``ValueError``.
    현재 인덱스 범위(0~7) 에서는 절대 boundary 초과가 발생하지 않지만,
    한도 검증을 한 곳에 두어 미래 확장(메시지 ID prefix 등) 시 회귀 방지.
    """
    payload = f"{_CALLBACK_PREFIX}:{option_index}"
    encoded = payload.encode("utf-8")
    if len(encoded) > MAX_CALLBACK_DATA_BYTES:
        raise ValueError(
            f"callback_data {payload!r} exceeds {MAX_CALLBACK_DATA_BYTES} "
            f"bytes (got {len(encoded)})"
        )
    return payload


def decode_callback_data(data: str) -> int | None:
    """``c:<idx>`` 페이로드에서 옵션 인덱스를 회수한다. 부적합하면 None.

    잘못된 prefix / 음수 / 정수 아닌 후미는 모두 None — 호출자(채널) 가 silently
    drop 하도록 한다 (외부 사용자가 임의의 callback_data 를 위조해도 안전).
    """
    if not isinstance(data, str):
        return None
    if not data.startswith(f"{_CALLBACK_PREFIX}:"):
        return None
    suffix = data[len(_CALLBACK_PREFIX) + 1:]
    try:
        idx = int(suffix)
    except ValueError:
        return None
    if idx < 0:
        return None
    return idx


# 오케스트레이터 ↔ 도구 핸들러 사이를 잇는 chat_id 운반체. 기본은 None — cron
# 메시지처럼 chat_id 가 없는 진입점에서 호출되면 핸들러가 안전하게 거부할 수 있다.
clarify_chat_id_var: ContextVar[int | None] = ContextVar(
    "clarify_chat_id", default=None,
)
