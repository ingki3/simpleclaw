"""Persona, agent, recipe, asset selector, sub-agent config loaders.

Agent runtime 주변 설정을 한 모듈에 모아 facade에서 재-export한다.
"""

from __future__ import annotations

from pathlib import Path

import yaml

# 페르소나 엔진 기본 설정값
# BIZ-313: 페르소나 파일(AGENT/USER/MEMORY)도 배포 repo(`~/.simpleclaw`)가
# 아니라 런타임 루트(`~/.simpleclaw-agent/default`)에서 읽는다. git 작업과
# dreaming 런타임 쓰기가 같은 디렉터리를 공유하지 않게 해 BIZ-28 류의 race 가
# *발생할 수 없도록* 만들기 위함.
_DEFAULTS = {
    "token_budget": 4096,
    "local_dir": "~/.simpleclaw-agent/default",
    "global_dir": "~/.agents/main",
    "files": [
        {"name": "AGENT.md", "type": "agent"},
        {"name": "USER.md", "type": "user"},
        {"name": "MEMORY.md", "type": "memory"},
    ],
}


def load_persona_config(config_path: str | Path) -> dict:
    """config.yaml에서 페르소나 엔진 설정을 로드한다.

    파일이 없거나 persona 키가 없으면 기본값을 반환한다.
    """
    config_path = Path(config_path)
    if not config_path.is_file():
        return dict(_DEFAULTS)

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return dict(_DEFAULTS)

    if not isinstance(data, dict):
        return dict(_DEFAULTS)

    persona = data.get("persona", {})
    if not isinstance(persona, dict):
        return dict(_DEFAULTS)

    return {
        "token_budget": persona.get("token_budget", _DEFAULTS["token_budget"]),
        "local_dir": persona.get("local_dir", _DEFAULTS["local_dir"]),
        "global_dir": persona.get("global_dir", _DEFAULTS["global_dir"]),
        "files": persona.get("files", _DEFAULTS["files"]),
    }


# 에이전트 오케스트레이터 기본 설정값
# BIZ-313: 대화 DB / 스킬 워크스페이스도 런타임 디렉터리
# (`~/.simpleclaw-agent/default`) 아래에 둔다. 배포 repo(`~/.simpleclaw`)에는
# SQLite WAL/SHM 파일이 더 이상 존재하지 않게 된다.
_AGENT_DEFAULTS: dict = {
    "history_limit": 20,
    "db_path": "~/.simpleclaw-agent/default/conversations.db",
    "max_tool_iterations": 15,
    "workspace_dir": "~/.simpleclaw-agent/default/workspace",
    # BIZ-162: web_fetch 의 헤드리스 폴백 경로 — None 이면 PATH + 알려진 후보 경로
    # 자동 탐색. nohup 등 PATH 가 축소된 데몬 환경에서 운영자가 명시적으로 지정.
    "web_fetch": {
        "headless_binary": None,
    },
    "asset_selection": {
        "enabled": False,
        "backend": "gemini",
        "skill_top_k": 5,
        "recipe_top_k": 3,
        "min_confidence": 0.5,
        "bypass_below_count": 8,
        "fallback_top_k": 12,
        "max_tokens": 512,
    },
}


def load_agent_config(config_path: str | Path) -> dict:
    """config.yaml에서 에이전트 오케스트레이터 설정을 로드한다."""
    config_path = Path(config_path)
    if not config_path.is_file():
        return _agent_with_defaults({})

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return _agent_with_defaults({})

    if not isinstance(data, dict):
        return _agent_with_defaults({})

    agent = data.get("agent", {})
    if not isinstance(agent, dict):
        return _agent_with_defaults({})

    return _agent_with_defaults(agent)


def _agent_with_defaults(agent: dict) -> dict:
    """에이전트 설정 dict 를 기본값으로 보강해 반환."""
    web_fetch = agent.get("web_fetch", {})
    if not isinstance(web_fetch, dict):
        web_fetch = {}

    headless_binary = web_fetch.get(
        "headless_binary",
        _AGENT_DEFAULTS["web_fetch"]["headless_binary"],
    )
    # 빈 문자열은 미설정으로 간주해 자동 탐색에 맡긴다 — 운영자가 일부러 빈 문자열을
    # 박는 경우는 없고, 대개 sed/yq 등으로 키만 비워둔 사고일 가능성.
    if isinstance(headless_binary, str) and not headless_binary.strip():
        headless_binary = None

    asset_selection = agent.get("asset_selection", {})
    if not isinstance(asset_selection, dict):
        asset_selection = {}
    asset_defaults = _AGENT_DEFAULTS["asset_selection"]

    return {
        "history_limit": agent.get(
            "history_limit", _AGENT_DEFAULTS["history_limit"]
        ),
        "db_path": agent.get("db_path", _AGENT_DEFAULTS["db_path"]),
        "max_tool_iterations": agent.get(
            "max_tool_iterations", _AGENT_DEFAULTS["max_tool_iterations"]
        ),
        "workspace_dir": agent.get(
            "workspace_dir", _AGENT_DEFAULTS["workspace_dir"]
        ),
        "web_fetch": {
            "headless_binary": headless_binary,
        },
        "asset_selection": {
            "enabled": bool(asset_selection.get("enabled", asset_defaults["enabled"])),
            "backend": asset_selection.get("backend", asset_defaults["backend"]),
            "skill_top_k": asset_selection.get("skill_top_k", asset_defaults["skill_top_k"]),
            "recipe_top_k": asset_selection.get("recipe_top_k", asset_defaults["recipe_top_k"]),
            "min_confidence": asset_selection.get(
                "min_confidence", asset_defaults["min_confidence"]
            ),
            "bypass_below_count": asset_selection.get(
                "bypass_below_count", asset_defaults["bypass_below_count"]
            ),
            "fallback_top_k": asset_selection.get(
                "fallback_top_k", asset_defaults["fallback_top_k"]
            ),
            "max_tokens": asset_selection.get("max_tokens", asset_defaults["max_tokens"]),
        },
    }


# 레시피 디렉터리 기본 설정값 (BIZ-202/BIZ-313)
# 봇이 채팅에서 만든 레시피가 데몬에도 곧장 보이도록, 작성 경로와 로드 경로를
# 절대 경로로 통일한다. 기본은 `~/.simpleclaw-agent/default/recipes/` — 다른 사용자 데이터
# (`conversations.db`, `daemon.db`, `MEMORY.md`, `workspace/`) 와 같은 운영 디렉터리
# 아래로 모은다. 레거시 `.agent/recipes/` 는 로더의 한 번 fallback 으로 살아 있다.
_RECIPES_DEFAULTS: dict = {
    "dir": "~/.simpleclaw-agent/default/recipes",
}


def load_recipes_config(config_path: str | Path) -> dict:
    """config.yaml 에서 레시피 디렉터리 설정을 로드한다 (BIZ-202).

    파일이 없거나 recipes 키가 없으면 기본 경로
    ``~/.simpleclaw-agent/default/recipes`` 를
    반환한다. 호출자는 ``Path(...).expanduser()`` 로 ``~`` 를 풀어야 한다.
    """
    config_path = Path(config_path)
    if not config_path.is_file():
        return dict(_RECIPES_DEFAULTS)

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return dict(_RECIPES_DEFAULTS)

    if not isinstance(data, dict):
        return dict(_RECIPES_DEFAULTS)

    recipes = data.get("recipes", {})
    if not isinstance(recipes, dict):
        return dict(_RECIPES_DEFAULTS)

    return {
        "dir": recipes.get("dir", _RECIPES_DEFAULTS["dir"]),
    }


# Asset selector 기본 설정값 (BIZ-311)
# 운영 기본은 disabled — selector는 main LLM의 후보군을 줄이는 보조 경로일 뿐,
# 실패하거나 꺼져 있으면 기존 전체 스킬/레시피 컨텍스트로 회귀해야 한다.
_ASSET_SELECTION_DEFAULTS: dict = {
    "enabled": False,
    "backend": "gemini",
    "skill_top_k": 5,
    "recipe_top_k": 3,
    "min_confidence": 0.5,
    "bypass_below_count": 8,
    "fallback_top_k": 50,
    "max_tokens": 512,
}


def _coerce_int_config(raw: object, default: int, *, minimum: int = 0) -> int:
    """정수 설정값을 안전하게 정규화한다."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def _coerce_float_config(
    raw: object,
    default: float,
    *,
    minimum: float = 0.0,
    maximum: float = 1.0,
) -> float:
    """실수 설정값을 지정 범위로 clamp한다."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return min(max(value, minimum), maximum)


def load_asset_selection_config(config_path: str | Path) -> dict:
    """config.yaml의 ``asset_selection`` 블록을 기본값으로 보강해 로드한다."""
    config_path = Path(config_path)
    if not config_path.is_file():
        return dict(_ASSET_SELECTION_DEFAULTS)

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return dict(_ASSET_SELECTION_DEFAULTS)

    if not isinstance(data, dict):
        return dict(_ASSET_SELECTION_DEFAULTS)

    # BIZ-311: 운영 config는 agent.asset_selection 아래에 둔다. 과거 스파이크/문서에서
    # 최상위 asset_selection을 쓴 경우도 읽어 테스트·수동 실험과의 호환성을 보존한다.
    agent = data.get("agent", {})
    raw = agent.get("asset_selection", {}) if isinstance(agent, dict) else {}
    if not raw:
        raw = data.get("asset_selection", {})
    if not isinstance(raw, dict):
        return dict(_ASSET_SELECTION_DEFAULTS)

    backend = raw.get("backend", _ASSET_SELECTION_DEFAULTS["backend"])
    if isinstance(backend, str):
        backend = backend.strip() or _ASSET_SELECTION_DEFAULTS["backend"]
    else:
        backend = _ASSET_SELECTION_DEFAULTS["backend"]

    return {
        "enabled": bool(raw.get("enabled", _ASSET_SELECTION_DEFAULTS["enabled"])),
        "backend": backend,
        "skill_top_k": _coerce_int_config(
            raw.get("skill_top_k", _ASSET_SELECTION_DEFAULTS["skill_top_k"]),
            _ASSET_SELECTION_DEFAULTS["skill_top_k"],
            minimum=0,
        ),
        "recipe_top_k": _coerce_int_config(
            raw.get("recipe_top_k", _ASSET_SELECTION_DEFAULTS["recipe_top_k"]),
            _ASSET_SELECTION_DEFAULTS["recipe_top_k"],
            minimum=0,
        ),
        "min_confidence": _coerce_float_config(
            raw.get("min_confidence", _ASSET_SELECTION_DEFAULTS["min_confidence"]),
            _ASSET_SELECTION_DEFAULTS["min_confidence"],
        ),
        "bypass_below_count": _coerce_int_config(
            raw.get("bypass_below_count", _ASSET_SELECTION_DEFAULTS["bypass_below_count"]),
            _ASSET_SELECTION_DEFAULTS["bypass_below_count"],
            minimum=0,
        ),
        "fallback_top_k": _coerce_int_config(
            raw.get("fallback_top_k", _ASSET_SELECTION_DEFAULTS["fallback_top_k"]),
            _ASSET_SELECTION_DEFAULTS["fallback_top_k"],
            minimum=1,
        ),
        "max_tokens": _coerce_int_config(
            raw.get("max_tokens", _ASSET_SELECTION_DEFAULTS["max_tokens"]),
            _ASSET_SELECTION_DEFAULTS["max_tokens"],
            minimum=1,
        ),
    }

# 서브 에이전트 기본 설정값
_SUB_AGENTS_DEFAULTS: dict = {
    "max_concurrent": 3,
    "default_timeout": 300,
    "workspace_dir": "workspace/sub_agents",
    "cleanup_workspace": False,
    "default_scope": {
        "allowed_paths": [],
        "network": False,
    },
}


def load_security_config(config_path: str | Path) -> dict:
    """config.yaml에서 security 섹션을 로드한다.

    BIZ-302 후속 — ``vault_path`` / ``master_key_path`` 키가 있으면 ``~`` 확장 후
    절대경로로 반환한다. 두 키는 ``EncryptedFileBackend`` 의 시크릿 볼트와 마스터
    키 파일 위치를 가리키며, 부트스트랩(``configure_default_manager``)에 전달된다.
    """
    config_path = Path(config_path)
    if not config_path.is_file():
        return {}

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return {}

    if not isinstance(data, dict):
        return {}

    sec = data.get("security", {})
    if not isinstance(sec, dict):
        return {}

    for key in ("vault_path", "master_key_path"):
        value = sec.get(key)
        if isinstance(value, str) and value:
            sec[key] = str(Path(value).expanduser())
        elif value is not None:
            sec[key] = None
    return sec


def load_sub_agents_config(config_path: str | Path) -> dict:
    """config.yaml에서 서브 에이전트 설정을 로드한다."""
    config_path = Path(config_path)
    if not config_path.is_file():
        return dict(_SUB_AGENTS_DEFAULTS)

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return dict(_SUB_AGENTS_DEFAULTS)

    if not isinstance(data, dict):
        return dict(_SUB_AGENTS_DEFAULTS)

    sa = data.get("sub_agents", {})
    if not isinstance(sa, dict):
        return dict(_SUB_AGENTS_DEFAULTS)

    default_scope = sa.get("default_scope", {})
    if not isinstance(default_scope, dict):
        default_scope = {}

    return {
        "max_concurrent": sa.get(
            "max_concurrent", _SUB_AGENTS_DEFAULTS["max_concurrent"]
        ),
        "default_timeout": sa.get(
            "default_timeout", _SUB_AGENTS_DEFAULTS["default_timeout"]
        ),
        "workspace_dir": sa.get(
            "workspace_dir", _SUB_AGENTS_DEFAULTS["workspace_dir"]
        ),
        "cleanup_workspace": sa.get(
            "cleanup_workspace", _SUB_AGENTS_DEFAULTS["cleanup_workspace"]
        ),
        "default_scope": {
            "allowed_paths": default_scope.get(
                "allowed_paths",
                _SUB_AGENTS_DEFAULTS["default_scope"]["allowed_paths"],
            ),
            "network": default_scope.get(
                "network",
                _SUB_AGENTS_DEFAULTS["default_scope"]["network"],
            ),
        },
    }
