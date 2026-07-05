"""MCP configuration loader.

MCP 서버 정의는 외부 프로세스를 실행하므로 기본값은 disabled이고,
서버별 command/env/scope를 명시적으로 검증한다. 실제 secret reference 해석은
MCPManager 실행 경계에서 수행한다.

설계 결정:
- malformed server는 조용히 제외한다 — 한 서버 설정 실수가 전체 런타임 부팅을
  막지 않도록 하고, 상세 오류 노출은 MCPManager/asset_inventory가 담당한다.
- MVP transport는 ``stdio``만 허용한다. 그 외 transport 서버는 drop된다.
- ``scope`` 기본값은 ``operator`` — MCP 서버는 외부 코드이므로 일반 사용자
  runtime 노출은 명시적인 ``scope: runtime`` opt-in으로만 허용한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_MCP_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "servers": {},
}

_ALLOWED_SCOPES = {"runtime", "operator"}
_ALLOWED_TRANSPORTS = {"stdio"}


def load_mcp_config(config_path: str | Path) -> dict[str, Any]:
    """config.yaml에서 MCP 설정을 로드한다.

    Args:
        config_path: config.yaml 경로.

    Returns:
        ``enabled``/``servers`` 키를 가진 정규화된 MCP 설정. 파일이 없거나
        섹션이 malformed이면 안전한 기본값(disabled/empty)을 반환한다.
    """
    path = Path(config_path)
    if not path.is_file():
        return dict(_MCP_DEFAULTS)

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return dict(_MCP_DEFAULTS)

    if not isinstance(data, dict):
        return dict(_MCP_DEFAULTS)

    raw = data.get("mcp", {})
    if not isinstance(raw, dict):
        return dict(_MCP_DEFAULTS)

    servers: dict[str, dict[str, Any]] = {}
    raw_servers = raw.get("servers", {})
    if isinstance(raw_servers, dict):
        for name, server in raw_servers.items():
            if not isinstance(name, str) or not isinstance(server, dict):
                continue
            normalized = _normalize_server(server)
            if normalized is not None:
                servers[name] = normalized

    return {
        # enabled 미지정 시 유효 서버가 있으면 켜진 것으로 본다 — 서버를 적어놓고
        # enabled를 빠뜨린 설정 실수가 조용한 no-op이 되지 않도록.
        "enabled": bool(raw.get("enabled", bool(servers))),
        "servers": servers,
    }


def _normalize_server(server: dict[str, Any]) -> dict[str, Any] | None:
    """단일 서버 설정을 검증/정규화한다. 사용할 수 없는 shape이면 None."""
    transport = str(server.get("transport") or "stdio").strip().lower()
    if transport not in _ALLOWED_TRANSPORTS:
        return None

    command = server.get("command")
    if not isinstance(command, str) or not command.strip():
        return None

    args = server.get("args", [])
    if not isinstance(args, list):
        args = []
    args = [str(item) for item in args]

    env = server.get("env", {})
    if not isinstance(env, dict):
        env = {}
    env = {str(k): str(v) for k, v in env.items()}

    scope = str(server.get("scope") or "operator").strip().lower()
    if scope not in _ALLOWED_SCOPES:
        scope = "operator"

    return {
        "transport": transport,
        "command": command.strip(),
        "args": args,
        "env": env,
        "timeout": _positive_int(server.get("timeout"), 120),
        "connect_timeout": _positive_int(server.get("connect_timeout"), 30),
        "scope": scope,
    }


def _positive_int(value: Any, default: int) -> int:
    """양의 정수만 허용하고 그 외는 default로 폴백한다."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


__all__ = ["_MCP_DEFAULTS", "load_mcp_config"]
