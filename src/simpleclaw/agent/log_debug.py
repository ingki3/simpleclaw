"""운영자용 로그 진단 native tool.

``log_debug``는 운영자가 live bot 로그를 read-only로 조회해 빈 응답, tool loop,
recipe/skill 실패, Telegram/Admin API 문제를 빠르게 좁히기 위한 도구다. 로그에는
토큰·사용자 메시지 원문이 섞일 수 있으므로 모든 반환 라인은 redaction과 길이 제한을
거친다. 파일을 쓰거나 프로세스를 건드리지 않고 지정된 로그 파일을 읽기만 한다.
"""

from __future__ import annotations

import json
import re
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any

DEFAULT_LOG_PATH = Path("/Users/simplist/.simpleclaw-agent/default/bot.log")

_ALLOWED_ACTIONS = frozenset({
    "recent",
    "errors",
    "trace",
    "tool_loop",
    "recipe",
    "skill",
    "telegram",
    "admin_api",
    "scheduler",
})
_DEFAULT_LINES = 80
_MAX_LINES = 200
_MAX_LINE_CHARS = 600
_MAX_PATTERN_CHARS = 120
_ACTION_PATTERNS: dict[str, tuple[str, ...]] = {
    "errors": ("error", "exception", "traceback", "failed", "failure"),
    "tool_loop": ("tool_loop", "tool loop", "max_tool_iterations", "tool call", "budget"),
    "recipe": ("recipe",),
    "skill": ("skill", "execute_skill"),
    "telegram": ("telegram", "sendmessage", "polling", "update_id"),
    "admin_api": ("admin_api", "admin api", "/admin/v1", "audit"),
    "scheduler": ("scheduler", "cron", "apscheduler", "job"),
}
_SECRET_KEY_RE = re.compile(
    r"(?i)\b(token|secret|password|api[_-]?key|authorization)(\s*[=:]\s*)([^\s,;]+)"
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+|"
    r"(\b\d{6,}:[A-Za-z0-9_-]{20,}\b)|"
    r"(gh[pousr]_[A-Za-z0-9_]+)|"
    r"(sk-[A-Za-z0-9._-]{8,})|"
    r"(AIza[0-9A-Za-z_-]{10,})|"
    r"([A-Za-z0-9_]{16,}:[A-Za-z0-9_\-]{20,})"
)
_USER_CONTENT_RE = re.compile(
    r"(?i)(message|text|content|prompt|user_input|body)([=:]\s*)(['\"]?)(.{80,})"
)


def handle_log_debug(
    args: dict[str, Any],
    *,
    log_path: str | Path = DEFAULT_LOG_PATH,
) -> str:
    """Function Calling 핸들러용 JSON 문자열을 반환한다.

    Args:
        args: ``action``/``lines``/``pattern``/``trace_id`` 옵션을 담은 tool arguments.
        log_path: 읽을 로그 파일. 테스트 주입 외에는 운영 기본 bot.log를 사용한다.

    Returns:
        시크릿과 긴 사용자 본문이 마스킹된 JSON 문자열. 로그 파일이 없거나 읽기
        실패해도 예외 대신 LLM-readable ``error`` 필드를 반환한다.
    """
    path = Path(log_path).expanduser()
    action = _normalize_action(args.get("action"))
    limit = _normalize_lines(args.get("lines"))
    pattern = _normalize_pattern(args.get("pattern"))
    trace_id = _normalize_pattern(args.get("trace_id"))

    raw_lines, error = _read_log_lines(path, tail_only=action == "recent" and not pattern and not trace_id)
    selected = _select_lines(raw_lines, action=action, pattern=pattern, trace_id=trace_id)
    selected = selected[-limit:]
    redacted = [_redact_line(line) for line in selected]

    payload: dict[str, Any] = {
        "ok": error is None,
        "read_only": True,
        "log_path": str(path),
        "action": action,
        "lines_requested": limit,
        "matched": len(selected),
        "truncated_to_last": limit if len(selected) == limit else None,
        "pattern": pattern,
        "trace_id": trace_id,
        "lines": redacted,
    }
    if error is not None:
        payload["error"] = error
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _normalize_action(raw: object) -> str:
    """허용 action 외 입력은 recent로 fail-closed 정규화한다."""
    action = str(raw or "recent")
    return action if action in _ALLOWED_ACTIONS else "recent"


def _normalize_lines(raw: object) -> int:
    """lines 값을 운영자 응답에 적합한 범위로 제한한다."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = _DEFAULT_LINES
    return max(1, min(_MAX_LINES, value))


def _normalize_pattern(raw: object) -> str | None:
    """pattern/trace_id를 정규식이 아닌 부분 문자열로 쓰기 위해 짧게 제한한다."""
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    return value[:_MAX_PATTERN_CHARS]


def _read_log_lines(path: Path, *, tail_only: bool) -> tuple[list[str], str | None]:
    """로그 파일을 읽어 줄 목록을 반환한다. 실패는 오류 문자열로 축약한다."""
    if not path.is_file():
        return [], f"log file not found: {path}"
    try:
        if tail_only:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                return list(deque((line.rstrip("\n") for line in handle), maxlen=_MAX_LINES)), None
        return path.read_text(encoding="utf-8", errors="replace").splitlines(), None
    except OSError as exc:
        return [], f"log read failed: {exc}"


def _select_lines(
    lines: Iterable[str],
    *,
    action: str,
    pattern: str | None,
    trace_id: str | None,
) -> list[str]:
    """action/pattern/trace_id 조건에 맞는 로그 줄만 고른다."""
    selected = list(lines)
    if action not in {"recent", "trace"}:
        needles = _ACTION_PATTERNS.get(action, ())
        selected = [line for line in selected if _contains_any(line, needles)]
    if action == "trace" and trace_id is None and pattern is None:
        selected = [line for line in selected if "trace" in line.lower()]
    if trace_id is not None:
        selected = [line for line in selected if trace_id in line]
    if pattern is not None:
        lower_pattern = pattern.lower()
        selected = [line for line in selected if lower_pattern in line.lower()]
    return selected


def _contains_any(line: str, needles: tuple[str, ...]) -> bool:
    """대소문자를 무시하고 action 키워드 포함 여부를 확인한다."""
    lower = line.lower()
    return any(needle in lower for needle in needles)


def _redact_line(line: str) -> str:
    """한 줄에서 대표 시크릿과 과도하게 긴 사용자 본문을 제거한다."""
    value = _SECRET_KEY_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", line)
    value = _SECRET_VALUE_RE.sub(lambda m: f"{m.group(1) or ''}[REDACTED]", value)
    value = _USER_CONTENT_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}[CLIPPED {len(m.group(4))} chars]",
        value,
    )
    if len(value) > _MAX_LINE_CHARS:
        return f"{value[:_MAX_LINE_CHARS]}... [clipped {len(value) - _MAX_LINE_CHARS} chars]"
    return value


__all__ = ["DEFAULT_LOG_PATH", "handle_log_debug"]
