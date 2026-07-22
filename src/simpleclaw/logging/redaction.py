"""Secret redaction for standard-library logging records.

Telegram embeds the bot token in both Bot API and file-download URL paths.  HTTP
client INFO logs therefore need to be sanitised before a formatter renders a
``LogRecord`` (including its deferred ``%s`` arguments and exception text).
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
    """Replace Telegram bot-token URL segments while preserving the URL shape."""

    return _TELEGRAM_BOT_URL_RE.sub(
        rf"\g<prefix>{TELEGRAM_TOKEN_MARKER}",
        value,
    )


def _redact_value(value: Any) -> Any:
    """Redact strings nested in common logging argument containers."""

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
    return value


class TelegramTokenRedactionFilter(logging.Filter):
    """Remove Telegram bot tokens from a record before formatter evaluation."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_value(record.msg)
        record.args = _redact_value(record.args)

        # Formatter creates ``exc_text`` after filters normally run.  Render it
        # here so exception messages containing a request URL are also safe.
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)

        # Cover exc_text, stack_info, and custom string/container extras.  The
        # transformation is a no-op for ordinary URLs and standard metadata.
        for name, value in tuple(record.__dict__.items()):
            if name == "exc_info":
                continue
            record.__dict__[name] = _redact_value(value)
        return True


def _install_filter(target: logging.Filterer) -> None:
    if not any(isinstance(item, TelegramTokenRedactionFilter) for item in target.filters):
        target.addFilter(TelegramTokenRedactionFilter())


def install_telegram_token_redaction(
    *,
    root_logger: logging.Logger | None = None,
    httpx_logger: logging.Logger | None = None,
) -> None:
    """Install idempotent redaction on root/httpx loggers and their handlers."""

    root = root_logger or logging.getLogger()
    httpx = httpx_logger or logging.getLogger("httpx")

    # Root handlers see propagated third-party records; the logger filters also
    # protect records emitted directly by root/httpx when no handler is present.
    for logger in (root, httpx):
        _install_filter(logger)
        for handler in logger.handlers:
            _install_filter(handler)
