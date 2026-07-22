"""표준 라이브러리 로그 레코드에서 비밀값을 마스킹합니다.

Telegram은 Bot API와 파일 다운로드 URL 경로에 봇 토큰을 포함합니다.
따라서 HTTP 클라이언트 INFO 로그는 지연된 ``%s`` 인자와 예외 텍스트를
포함해 포매터가 ``LogRecord``를 렌더링하기 전에 정리해야 합니다.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any


TELEGRAM_TOKEN_MARKER = "<redacted>"

_TELEGRAM_BOT_URL_RE = re.compile(
    r"(?P<prefix>(?:https?://)?api\.telegram\.org/(?:file/)?bot)"
    r"[^/?#\s\"'<>]+",
    re.IGNORECASE,
)


def redact_telegram_bot_tokens(value: str) -> str:
    """URL 형태를 유지하면서 Telegram 봇 토큰 경로를 마스킹합니다."""

    return _TELEGRAM_BOT_URL_RE.sub(
        rf"\g<prefix>{TELEGRAM_TOKEN_MARKER}",
        value,
    )


def _redact_value(value: Any) -> Any:
    """문자열과 지연 로깅 인자에 포함된 비밀값을 재귀적으로 마스킹합니다."""

    if isinstance(value, str):
        return redact_telegram_bot_tokens(value)
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, Mapping):
        return {
            _redact_value(key): _redact_value(item)
            for key, item in value.items()
        }

    # 로깅은 포매팅할 때까지 ``%s`` 변환을 미루므로 httpx 같은 외부 클라이언트는
    # 문자열 대신 URL 객체를 ``record.args``로 전달합니다. 일반 객체와 숫자형
    # ``%d``/``%f`` 의미는 유지하고, 렌더링 결과에 Telegram 인증 정보가 실제로
    # 포함된 객체만 마스킹된 문자열로 바꿉니다.
    try:
        rendered = str(value)
    except Exception:
        return value
    redacted = redact_telegram_bot_tokens(rendered)
    if redacted != rendered:
        return redacted
    return value


class TelegramTokenRedactionFilter(logging.Filter):
    """포매터가 평가하기 전에 레코드의 Telegram 봇 토큰을 제거합니다."""

    def filter(self, record: logging.LogRecord) -> bool:
        """지연 포매팅과 예외 정보까지 안전하도록 레코드 전체를 정리합니다."""

        record.msg = _redact_value(record.msg)
        record.args = _redact_value(record.args)

        # 포매터는 보통 필터 실행 후 ``exc_text``를 만들므로, 요청 URL이 포함된
        # 예외 메시지도 보호할 수 있도록 여기에서 먼저 렌더링합니다.
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)

        # exc_text, stack_info와 사용자 정의 부가 필드까지 빠짐없이 보호합니다.
        # 일반 URL과 표준 메타데이터에는 변환이 적용되지 않습니다.
        for name, value in tuple(record.__dict__.items()):
            if name == "exc_info":
                continue
            record.__dict__[name] = _redact_value(value)
        return True


def _install_filter(target: logging.Filterer) -> None:
    """같은 필터를 중복 설치하지 않아 반복 초기화를 안전하게 유지합니다."""

    if not any(isinstance(item, TelegramTokenRedactionFilter) for item in target.filters):
        target.addFilter(TelegramTokenRedactionFilter())


def install_telegram_token_redaction(
    *,
    root_logger: logging.Logger | None = None,
    httpx_logger: logging.Logger | None = None,
) -> None:
    """root/httpx 로거와 핸들러에 멱등한 마스킹 필터를 설치합니다."""

    root = root_logger or logging.getLogger()
    httpx = httpx_logger or logging.getLogger("httpx")

    # root 핸들러는 전파된 외부 라이브러리 레코드를 처리하며, 로거 필터는 핸들러가
    # 없을 때 root/httpx에서 직접 발생한 레코드도 보호합니다.
    for logger in (root, httpx):
        _install_filter(logger)
        for handler in logger.handlers:
            _install_filter(handler)
