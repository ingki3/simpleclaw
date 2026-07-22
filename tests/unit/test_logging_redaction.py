"""BIZ-473: Telegram bot tokens must not reach formatted logs."""

from __future__ import annotations

import logging
from io import StringIO
import sys

import httpx
import pytest

from simpleclaw.logging.redaction import (
    TELEGRAM_TOKEN_MARKER,
    TelegramTokenRedactionFilter,
    install_telegram_token_redaction,
)


SYNTHETIC_TOKEN = "123456789:synthetic_bot_token_value_for_tests"


def _format_record(
    message: object,
    args: tuple[object, ...] | dict[str, object] = (),
    **extra: object,
) -> str:
    record_args = (args,) if isinstance(args, dict) else args
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=record_args,
        exc_info=extra.pop("exc_info", None),
    )
    for key, value in extra.items():
        setattr(record, key, value)
    assert TelegramTokenRedactionFilter().filter(record)
    return logging.Formatter("%(message)s %(request_url)s").format(record)


@pytest.mark.parametrize(
    "url, expected_suffix",
    [
        (
            f"https://api.telegram.org/bot{SYNTHETIC_TOKEN}/getUpdates",
            "/getUpdates",
        ),
        (
            f"https://api.telegram.org/file/bot{SYNTHETIC_TOKEN}/photos/image.jpg",
            "/photos/image.jpg",
        ),
    ],
)
def test_direct_message_redacts_bot_and_file_urls(url: str, expected_suffix: str):
    rendered = _format_record(url, request_url="safe")

    assert SYNTHETIC_TOKEN not in rendered
    assert f"bot{TELEGRAM_TOKEN_MARKER}{expected_suffix}" in rendered


def test_deferred_args_are_redacted_before_formatter():
    url = f"https://api.telegram.org/bot{SYNTHETIC_TOKEN}/sendMessage"

    rendered = _format_record("HTTP Request: %s %s", ("POST", url), request_url="safe")

    assert rendered.startswith("HTTP Request: POST ")
    assert SYNTHETIC_TOKEN not in rendered
    assert f"bot{TELEGRAM_TOKEN_MARKER}/sendMessage" in rendered


@pytest.mark.parametrize(
    "path",
    [
        "bot{token}/sendMessage",
        "file/bot{token}/photos/image.jpg",
    ],
)
def test_httpx_url_deferred_arg_is_redacted(path: str):
    url = httpx.URL(
        f"https://api.telegram.org/{path.format(token=SYNTHETIC_TOKEN)}"
    )

    rendered = _format_record("HTTP Request: %s %s", ("POST", url), request_url="safe")

    assert SYNTHETIC_TOKEN not in rendered
    assert TELEGRAM_TOKEN_MARKER in rendered
    assert rendered.startswith("HTTP Request: POST ")


@pytest.mark.parametrize(
    "path",
    [
        "bot{token}/sendMessage",
        "file/bot{token}/photos/image.jpg",
    ],
)
def test_real_httpx_info_log_redacts_url_and_preserves_diagnostics(path: str):
    logger = logging.getLogger("httpx")
    original_filters = logger.filters[:]
    original_handlers = logger.handlers[:]
    original_level = logger.level
    original_propagate = logger.propagate
    output = StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(logging.Formatter("%(message)s"))

    try:
        logger.handlers = [handler]
        logger.filters = []
        logger.setLevel(logging.INFO)
        logger.propagate = False
        install_telegram_token_redaction(
            root_logger=logging.Logger("isolated-root"),
            httpx_logger=logger,
        )

        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, request=request)

        url = f"https://api.telegram.org/{path.format(token=SYNTHETIC_TOKEN)}"
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            response = client.post(url)

        rendered = output.getvalue()
    finally:
        logger.filters = original_filters
        logger.handlers = original_handlers
        logger.setLevel(original_level)
        logger.propagate = original_propagate

    assert response.status_code == 200
    assert SYNTHETIC_TOKEN not in rendered
    assert TELEGRAM_TOKEN_MARKER in rendered
    assert "HTTP Request: POST" in rendered
    assert "200 OK" in rendered


def test_mapping_args_and_extra_strings_are_redacted():
    url = f"https://api.telegram.org/file/bot{SYNTHETIC_TOKEN}/document.bin"

    rendered = _format_record("download=%(url)s", {"url": url}, request_url=url)

    assert SYNTHETIC_TOKEN not in rendered
    assert rendered.count(TELEGRAM_TOKEN_MARKER) == 2


def test_exception_text_is_redacted_before_formatter():
    url = f"https://api.telegram.org/bot{SYNTHETIC_TOKEN}/getMe"
    try:
        raise RuntimeError(f"request failed: {url}")
    except RuntimeError:
        exc_info = sys.exc_info()

    rendered = _format_record("failed", request_url="safe", exc_info=exc_info)

    assert SYNTHETIC_TOKEN not in rendered
    assert f"bot{TELEGRAM_TOKEN_MARKER}/getMe" in rendered


def test_non_telegram_url_is_unchanged():
    url = "https://example.com/bot-public-value/health?status=200"

    rendered = _format_record("HTTP Request: %s", (url,), request_url=url)

    assert rendered == f"HTTP Request: {url} {url}"


def test_non_secret_numeric_formatting_is_unchanged():
    rendered = _format_record(
        "status=%d elapsed=%.1f",
        (200, 1.25),
        request_url="safe",
    )

    assert rendered == "status=200 elapsed=1.2 safe"


def test_install_is_idempotent_for_loggers_and_handlers():
    root = logging.Logger("isolated-root")
    httpx = logging.Logger("isolated-httpx")
    root_handler = logging.StreamHandler()
    httpx_handler = logging.StreamHandler()
    root.addHandler(root_handler)
    httpx.addHandler(httpx_handler)

    install_telegram_token_redaction(root_logger=root, httpx_logger=httpx)
    install_telegram_token_redaction(root_logger=root, httpx_logger=httpx)

    for target in (root, httpx, root_handler, httpx_handler):
        filters = [
            item for item in target.filters
            if isinstance(item, TelegramTokenRedactionFilter)
        ]
        assert len(filters) == 1
