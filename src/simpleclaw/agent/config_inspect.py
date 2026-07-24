"""운영자용 effective config 요약 도구.

``config_inspect`` native tool은 운영자가 현재 프로세스가 바라보는 config.yaml의
주요 섹션을 read-only JSON으로 확인하기 위한 진단 도구다. live config와 dev config
혼동을 줄이기 위해 응답에 항상 ``config_path``를 포함하고, ``~`` 경로는 요청 시
절대 경로로 풀어 보여준다. 토큰/키 값은 출력하지 않되, ``file:admin_api_token`` 같은
시크릿 참조 문자열은 운영자가 wiring을 확인할 수 있도록 그대로 보존한다.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from simpleclaw.config import (
    _ADMIN_API_DEFAULTS,
    _AGENT_DEFAULTS,
    _DAEMON_DEFAULTS,
    _LLM_DEFAULTS,
    _MEMORY_DEFAULTS,
    _RECIPES_DEFAULTS,
)

DEFAULT_CONFIG_PATH = Path("/Users/simplist/.simpleclaw/config.yaml")

_SECTION_KEYS = (
    "llm",
    "agent",
    "memory",
    "skills",
    "recipes",
    "daemon",
    "admin_api",
    "security",
)
_ALLOWED_SECTIONS = frozenset(("all", *_SECTION_KEYS))
_DEFAULT_SECTIONS: dict[str, Any] = {
    "llm": _LLM_DEFAULTS,
    "agent": _AGENT_DEFAULTS,
    "memory": _MEMORY_DEFAULTS,
    "skills": {},
    "recipes": _RECIPES_DEFAULTS,
    "daemon": _DAEMON_DEFAULTS,
    "admin_api": _ADMIN_API_DEFAULTS,
    "security": {},
}
_SECRET_KEY_RE = re.compile(r"(token|secret|password|api[_-]?key|master[_-]?key)", re.IGNORECASE)
_SECRET_REF_RE = re.compile(r"^(env|file|keyring):[A-Za-z0-9_.:/@-]+$")
_SECRET_VALUE_RE = re.compile(
    r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+|"
    r"(gh[pousr]_[A-Za-z0-9_]+)|"
    r"(sk-[A-Za-z0-9._-]{6,})|"
    r"(AIza[0-9A-Za-z_-]{10,})|"
    r"([A-Za-z0-9_]{16,}:[A-Za-z0-9_\-]{20,})"
)
_PATH_KEY_RE = re.compile(r"(^|_)(path|dir|file)$|(_path|_dir|_file)$")


def handle_config_inspect(
    args: dict[str, Any],
    *,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
) -> str:
    """Function Calling 핸들러용 JSON 문자열을 반환한다.

    Args:
        args: ``section``/``resolve_paths``/``redact`` 옵션을 담은 tool arguments.
        config_path: live ``config.yaml`` 경로. 기본값은 운영 기본 경로다.

    Returns:
        요청 섹션만 포함한 read-only JSON 문자열. config 로드 실패도 JSON의
        ``error`` 필드로 축약해 도구 호출 자체가 예외로 터지지 않게 한다.
    """
    path = Path(config_path).expanduser()
    section = _normalize_section(args.get("section"))
    resolve_paths = bool(args.get("resolve_paths", False))
    redact = bool(args.get("redact", True))
    raw, error = _read_config(path)

    sections = _select_sections(raw, section)
    if resolve_paths:
        sections = {
            name: _resolve_paths(value)
            for name, value in sections.items()
        }
    if redact:
        sections = _redact(sections)

    payload: dict[str, Any] = {
        "ok": error is None,
        "read_only": True,
        "config_path": str(path),
        "section": section,
        "resolve_paths": resolve_paths,
        "redacted": redact,
        "sections": sections,
    }
    if error is not None:
        payload["error"] = error
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _normalize_section(raw: object) -> str:
    """section 입력을 허용 목록 안의 값으로 정규화한다."""
    section = str(raw or "all")
    return section if section in _ALLOWED_SECTIONS else "all"


def _read_config(path: Path) -> tuple[dict[str, Any], str | None]:
    """config.yaml을 dict로 읽고, 실패해도 빈 dict와 오류 문자열을 반환한다."""
    if not path.is_file():
        return {}, f"config file not found: {path}"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return {}, f"config load failed: {exc}"
    if not isinstance(data, dict):
        return {}, "config root is not a mapping"
    return data, None


def _select_sections(raw: dict[str, Any], section: str) -> dict[str, Any]:
    """raw config와 기본값을 합쳐 요청 섹션만 반환한다."""
    names = _SECTION_KEYS if section == "all" else (section,)
    selected: dict[str, Any] = {}
    for name in names:
        configured = raw.get(name, {})
        if not isinstance(configured, dict):
            configured = {}
        selected[name] = _deep_merge(deepcopy(_DEFAULT_SECTIONS[name]), configured)
    return selected


def _deep_merge(base: Any, override: Any) -> Any:
    """dict는 재귀 병합하고, 나머지 값은 override를 우선한다."""
    if not isinstance(base, dict) or not isinstance(override, dict):
        return deepcopy(override)
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _resolve_paths(value: Any, *, key: str = "") -> Any:
    """path/dir/file 계열 키의 ``~``를 절대 경로로 확장한다."""
    if isinstance(value, dict):
        return {str(k): _resolve_paths(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_paths(item, key=key) for item in value]
    if isinstance(value, str) and _PATH_KEY_RE.search(key):
        return str(Path(value).expanduser())
    return value


def _redact(value: Any, *, key: str = "") -> Any:
    """dict/list/string payload 안의 시크릿 키와 대표 시크릿 값을 재귀 마스킹한다."""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for child_key, child_value in value.items():
            redacted[str(child_key)] = _redact(child_value, key=str(child_key))
        return redacted
    if isinstance(value, list):
        return [_redact(item, key=key) for item in value]
    if isinstance(value, str):
        if _SECRET_KEY_RE.search(key) and not _SECRET_REF_RE.match(value):
            return "[REDACTED]" if value else value
        return _SECRET_VALUE_RE.sub(lambda m: f"{m.group(1) or ''}[REDACTED]", value)
    return value


__all__ = ["DEFAULT_CONFIG_PATH", "handle_config_inspect"]