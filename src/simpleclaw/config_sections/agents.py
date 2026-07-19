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
    # BIZ-451: SOUL.md(정체성·말투 tone guard)를 기본 파일 목록에 포함한다.
    # 파일이 없는 프로필에서는 resolver가 warning 후 graceful skip 하므로 안전.
    "files": [
        {"name": "SOUL.md", "type": "soul"},
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
    "browser_handoff": {
        "enabled": False,
        "chrome_app": "Google Chrome",
        "store_dir": "~/.simpleclaw-agent/default/browser-handoff",
        "request_ttl_seconds": 600,
        "open_wait_seconds": 90,
        "max_extracted_chars": 50000,
        "native_host_name": "com.simpleclaw.browser_handoff",
        "extension_id": None,
        "sensitive_domain_policy": "block",
        "allow_auto_extract": False,
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
    "goal_loop": {
        "enabled": True,
        "max_rounds": 3,
        "judge_max_tokens": 768,
        "max_answer_chars_for_judge": 6000,
    },
    "complex_fact_workflow": {
        "enabled": False,
        "route_threshold": 3,
        "max_iterations": 6,
        "max_sources_per_slot": 3,
        "planner_backend": "simpleclaw",
        "enable_claim_verifier": True,
        "enable_progress_events": True,
    },
    # BIZ-426: 일반 turn 앞단 LLM turn analysis. 기본 활성 — keyword heuristic
    # (TurnFrame/response_router)은 분석 비활성/실패 시의 fallback 으로만 동작.
    "turn_analysis": {
        "enabled": True,
        "backend": None,  # None 이면 llm.default backend 사용
        # BIZ-453: 최종 답변 default model 과 독립된 TurnAnalysis 전용 모델.
        # provider(기존 llm.providers 백엔드 이름) + model 이 모두 설정되면
        # backend/llm.default 대신 해당 provider credentials 로 model 만 바꾼
        # 가상 백엔드를 사용한다. 하나라도 없으면 기존 backend 동작 유지.
        "provider": None,
        "model": None,
        # BIZ-452: 512 cap 에서는 tail `reasons` 문자열이 잘려 structured JSON
        # 파싱이 실패하는 live 사고가 있었다. 분석 응답은 원래 짧아 실사용
        # 토큰은 그대로이므로 상한만 2048 로 여유 있게 올린다.
        "max_tokens": 2048,
        "max_recent_messages": 12,
        # BIZ-427: Gemini structured output(response_schema)으로 schema 준수
        # JSON 을 강제. False 는 프롬프트-only JSON 지시 escape hatch.
        "structured_output": True,
        # BIZ-452: 파싱+truncated-tail repair 까지 실패했을 때 1회 재시도할
        # 백엔드. None 이면 llm.fallback(router.get_fallback_backend())에 위임.
        "retry_backend": None,
        # BIZ-453: 재시도도 provider+model 로 독립 지정 가능. 둘 다 있으면
        # retry_backend 보다 우선한다.
        "retry_provider": None,
        "retry_model": None,
        # BIZ-453: provider-neutral reasoning hint. Gemini 는 native thinking
        # config 로 매핑하고, 미지원 provider/SDK 는 안전하게 무시한다.
        # 기본 off — 기존 배포 동작을 바꾸지 않는다.
        "reasoning": {
            "enabled": False,
            "effort": "medium",
            "budget_tokens": 512,
        },
        "fallback_mode": "conservative_original",
    },
}

# BIZ-453 — reasoning.effort 허용값. 밖의 값은 기본(medium)으로 정규화한다.
_REASONING_EFFORTS = {"low", "medium", "high"}


def _coerce_optional_name(raw: object) -> str | None:
    """provider/model 류 이름 설정을 정규화한다 — 빈 문자열/비문자열은 None."""
    if isinstance(raw, str):
        return raw.strip() or None
    return None


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

    browser_handoff = agent.get("browser_handoff", {})
    if not isinstance(browser_handoff, dict):
        browser_handoff = {}
    browser_defaults = _AGENT_DEFAULTS["browser_handoff"]
    extension_id = browser_handoff.get("extension_id", browser_defaults["extension_id"])
    if isinstance(extension_id, str) and not extension_id.strip():
        extension_id = None
    sensitive_policy = str(
        browser_handoff.get(
            "sensitive_domain_policy",
            browser_defaults["sensitive_domain_policy"],
        )
    )
    if sensitive_policy not in {"block", "ask"}:
        sensitive_policy = browser_defaults["sensitive_domain_policy"]

    asset_selection = agent.get("asset_selection", {})
    if not isinstance(asset_selection, dict):
        asset_selection = {}
    asset_defaults = _AGENT_DEFAULTS["asset_selection"]

    goal_loop = agent.get("goal_loop", {})
    if not isinstance(goal_loop, dict):
        goal_loop = {}
    goal_defaults = _AGENT_DEFAULTS["goal_loop"]

    complex_fact = agent.get("complex_fact_workflow", {})
    if not isinstance(complex_fact, dict):
        complex_fact = {}
    complex_defaults = _AGENT_DEFAULTS["complex_fact_workflow"]

    turn_analysis = agent.get("turn_analysis", {})
    if not isinstance(turn_analysis, dict):
        turn_analysis = {}
    turn_analysis_defaults = _AGENT_DEFAULTS["turn_analysis"]
    turn_analysis_backend = turn_analysis.get(
        "backend", turn_analysis_defaults["backend"]
    )
    # 빈 문자열 backend 는 미설정으로 간주 — 기본 LLM backend 로 라우팅한다.
    if isinstance(turn_analysis_backend, str) and not turn_analysis_backend.strip():
        turn_analysis_backend = None
    turn_analysis_retry_backend = turn_analysis.get(
        "retry_backend", turn_analysis_defaults["retry_backend"]
    )
    # 빈 문자열 retry_backend 도 미설정으로 간주 — llm.fallback 백엔드에 위임한다.
    if isinstance(turn_analysis_retry_backend, str) and (
        not turn_analysis_retry_backend.strip()
    ):
        turn_analysis_retry_backend = None
    # BIZ-453 — TurnAnalysis 전용 provider/model 및 reasoning hint 정규화.
    turn_analysis_reasoning = turn_analysis.get("reasoning", {})
    if not isinstance(turn_analysis_reasoning, dict):
        turn_analysis_reasoning = {}
    reasoning_defaults = turn_analysis_defaults["reasoning"]
    reasoning_effort = str(
        turn_analysis_reasoning.get("effort", reasoning_defaults["effort"])
    ).strip().lower()
    if reasoning_effort not in _REASONING_EFFORTS:
        reasoning_effort = reasoning_defaults["effort"]
    planner_backend = str(
        complex_fact.get("planner_backend", complex_defaults["planner_backend"])
    )
    if planner_backend not in {"simpleclaw", "dspy"}:
        planner_backend = complex_defaults["planner_backend"]

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
        "browser_handoff": {
            "enabled": bool(
                browser_handoff.get("enabled", browser_defaults["enabled"])
            ),
            "chrome_app": browser_handoff.get(
                "chrome_app", browser_defaults["chrome_app"]
            ),
            "store_dir": browser_handoff.get(
                "store_dir", browser_defaults["store_dir"]
            ),
            "request_ttl_seconds": _coerce_int_config(
                browser_handoff.get(
                    "request_ttl_seconds",
                    browser_defaults["request_ttl_seconds"],
                ),
                browser_defaults["request_ttl_seconds"],
                minimum=1,
            ),
            "open_wait_seconds": _coerce_int_config(
                browser_handoff.get(
                    "open_wait_seconds",
                    browser_defaults["open_wait_seconds"],
                ),
                browser_defaults["open_wait_seconds"],
                minimum=0,
            ),
            "max_extracted_chars": _coerce_int_config(
                browser_handoff.get(
                    "max_extracted_chars",
                    browser_defaults["max_extracted_chars"],
                ),
                browser_defaults["max_extracted_chars"],
                minimum=1000,
            ),
            "native_host_name": browser_handoff.get(
                "native_host_name", browser_defaults["native_host_name"]
            ),
            "extension_id": extension_id,
            "sensitive_domain_policy": sensitive_policy,
            "allow_auto_extract": bool(
                browser_handoff.get(
                    "allow_auto_extract", browser_defaults["allow_auto_extract"]
                )
            ),
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
        "goal_loop": {
            "enabled": bool(goal_loop.get("enabled", goal_defaults["enabled"])),
            "max_rounds": _coerce_int_config(
                goal_loop.get("max_rounds", goal_defaults["max_rounds"]),
                goal_defaults["max_rounds"],
                minimum=1,
            ),
            "judge_max_tokens": _coerce_int_config(
                goal_loop.get("judge_max_tokens", goal_defaults["judge_max_tokens"]),
                goal_defaults["judge_max_tokens"],
                minimum=200,
            ),
            "max_answer_chars_for_judge": _coerce_int_config(
                goal_loop.get(
                    "max_answer_chars_for_judge",
                    goal_defaults["max_answer_chars_for_judge"],
                ),
                goal_defaults["max_answer_chars_for_judge"],
                minimum=1000,
            ),
        },
        "complex_fact_workflow": {
            "enabled": bool(complex_fact.get("enabled", complex_defaults["enabled"])),
            "route_threshold": _coerce_int_config(
                complex_fact.get(
                    "route_threshold",
                    complex_defaults["route_threshold"],
                ),
                complex_defaults["route_threshold"],
                minimum=1,
            ),
            "max_iterations": _coerce_int_config(
                complex_fact.get("max_iterations", complex_defaults["max_iterations"]),
                complex_defaults["max_iterations"],
                minimum=1,
            ),
            "max_sources_per_slot": _coerce_int_config(
                complex_fact.get(
                    "max_sources_per_slot",
                    complex_defaults["max_sources_per_slot"],
                ),
                complex_defaults["max_sources_per_slot"],
                minimum=1,
            ),
            "planner_backend": planner_backend,
            "enable_claim_verifier": bool(
                complex_fact.get(
                    "enable_claim_verifier",
                    complex_defaults["enable_claim_verifier"],
                )
            ),
            "enable_progress_events": bool(
                complex_fact.get(
                    "enable_progress_events",
                    complex_defaults["enable_progress_events"],
                )
            ),
        },
        "turn_analysis": {
            "enabled": bool(
                turn_analysis.get("enabled", turn_analysis_defaults["enabled"])
            ),
            "backend": turn_analysis_backend,
            "provider": _coerce_optional_name(turn_analysis.get("provider")),
            "model": _coerce_optional_name(turn_analysis.get("model")),
            "max_tokens": _coerce_int_config(
                turn_analysis.get("max_tokens", turn_analysis_defaults["max_tokens"]),
                turn_analysis_defaults["max_tokens"],
                minimum=64,
            ),
            "max_recent_messages": _coerce_int_config(
                turn_analysis.get(
                    "max_recent_messages",
                    turn_analysis_defaults["max_recent_messages"],
                ),
                turn_analysis_defaults["max_recent_messages"],
                minimum=0,
            ),
            "structured_output": bool(
                turn_analysis.get(
                    "structured_output",
                    turn_analysis_defaults["structured_output"],
                )
            ),
            "retry_backend": turn_analysis_retry_backend,
            "retry_provider": _coerce_optional_name(
                turn_analysis.get("retry_provider")
            ),
            "retry_model": _coerce_optional_name(turn_analysis.get("retry_model")),
            "reasoning": {
                "enabled": bool(
                    turn_analysis_reasoning.get(
                        "enabled", reasoning_defaults["enabled"]
                    )
                ),
                "effort": reasoning_effort,
                "budget_tokens": _coerce_int_config(
                    turn_analysis_reasoning.get(
                        "budget_tokens", reasoning_defaults["budget_tokens"]
                    ),
                    reasoning_defaults["budget_tokens"],
                    minimum=0,
                ),
            },
            "fallback_mode": str(
                turn_analysis.get(
                    "fallback_mode", turn_analysis_defaults["fallback_mode"]
                )
            ),
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

_SKILL_LEARNING_DEFAULTS: dict = {
    "enabled": False,
    "min_tool_calls": 2,
    "min_distinct_tools": 2,
    "min_final_chars": 500,
    "suggestions_file": "~/.simpleclaw-agent/default/skill_suggestions.jsonl",
    "target_dir": None,
    # BIZ-432 — require_operator_accept 키 제거. materialize 의 accepted 게이트는
    # 설정으로 완화할 수 없으므로 config.yaml 에 남아 있어도 무시된다.
    "max_trace_observation_chars": 1200,
    # BIZ-429 — 후보 생성 시 운영자 알림 hook 호출 여부.
    "notify_on_candidate": True,
    # BIZ-429 — 후보 LLM 출력을 BIZ-427 schema-constrained JSON 으로 강제할지.
    "structured_output": True,
}

# BIZ-428 — recipe learning 기본 설정값.
# 운영 기본은 disabled — 켜도 후보는 pending 큐에만 쌓이는 approval-only 흐름이며,
# live recipes.dir 설치는 operator recipe_learning tool의 materialize 승인 경로만
# 수행한다. structured_output 은 BIZ-427 response_schema 기반 JSON 강제 게이트.
_RECIPE_LEARNING_DEFAULTS: dict = {
    "enabled": False,
    "min_tool_calls": 2,
    "min_distinct_tools": 2,
    "min_final_chars": 500,
    "suggestions_file": "~/.simpleclaw-agent/default/recipe_suggestions.jsonl",
    # BIZ-435 — require_operator_accept 키 제거. recipe materialize 의 accepted
    # 게이트는 설정으로 완화할 수 없으므로 config.yaml 에 남아 있어도 무시된다.
    "max_trace_observation_chars": 1200,
    "structured_output": True,
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


def load_skills_learning_config(config_path: str | Path) -> dict:
    """config.yaml의 ``skills.learning`` 블록을 안전한 기본값으로 보강해 로드한다."""
    config_path = Path(config_path)
    raw: dict = {}
    if config_path.is_file():
        try:
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            skills = data.get("skills", {}) if isinstance(data, dict) else {}
            raw = skills.get("learning", {}) if isinstance(skills, dict) else {}
        except (yaml.YAMLError, OSError):
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    defaults = _SKILL_LEARNING_DEFAULTS
    target_dir = raw.get("target_dir", defaults["target_dir"])
    if isinstance(target_dir, str) and not target_dir.strip():
        target_dir = None
    return {
        "enabled": bool(raw.get("enabled", defaults["enabled"])),
        "min_tool_calls": _coerce_int_config(raw.get("min_tool_calls", defaults["min_tool_calls"]), defaults["min_tool_calls"], minimum=1),
        "min_distinct_tools": _coerce_int_config(raw.get("min_distinct_tools", defaults["min_distinct_tools"]), defaults["min_distinct_tools"], minimum=1),
        "min_final_chars": _coerce_int_config(raw.get("min_final_chars", defaults["min_final_chars"]), defaults["min_final_chars"], minimum=0),
        "suggestions_file": raw.get("suggestions_file", defaults["suggestions_file"]),
        "target_dir": target_dir,
        "max_trace_observation_chars": _coerce_int_config(raw.get("max_trace_observation_chars", defaults["max_trace_observation_chars"]), defaults["max_trace_observation_chars"], minimum=200),
        "notify_on_candidate": bool(raw.get("notify_on_candidate", defaults["notify_on_candidate"])),
        "structured_output": bool(raw.get("structured_output", defaults["structured_output"])),
    }


def load_recipe_learning_config(config_path: str | Path) -> dict:
    """config.yaml의 ``recipes.learning`` 블록을 안전한 기본값으로 보강해 로드한다.

    skill learning 이 ``skills.learning`` 아래에 있듯이 recipe learning 은
    ``recipes.learning`` 아래에 둔다 — 후보 산출물(recipe.yaml)이 속한 서브시스템과
    설정 위치를 일치시킨다. 파일이 없거나 파싱에 실패하면 disabled 기본값으로
    돌아간다 (BIZ-428 approval-only safe default).
    """
    config_path = Path(config_path)
    raw: dict = {}
    if config_path.is_file():
        try:
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            recipes = data.get("recipes", {}) if isinstance(data, dict) else {}
            raw = recipes.get("learning", {}) if isinstance(recipes, dict) else {}
        except (yaml.YAMLError, OSError):
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    defaults = _RECIPE_LEARNING_DEFAULTS
    return {
        "enabled": bool(raw.get("enabled", defaults["enabled"])),
        "min_tool_calls": _coerce_int_config(raw.get("min_tool_calls", defaults["min_tool_calls"]), defaults["min_tool_calls"], minimum=1),
        "min_distinct_tools": _coerce_int_config(raw.get("min_distinct_tools", defaults["min_distinct_tools"]), defaults["min_distinct_tools"], minimum=1),
        "min_final_chars": _coerce_int_config(raw.get("min_final_chars", defaults["min_final_chars"]), defaults["min_final_chars"], minimum=0),
        "suggestions_file": raw.get("suggestions_file", defaults["suggestions_file"]),
        "max_trace_observation_chars": _coerce_int_config(raw.get("max_trace_observation_chars", defaults["max_trace_observation_chars"]), defaults["max_trace_observation_chars"], minimum=200),
        "structured_output": bool(raw.get("structured_output", defaults["structured_output"])),
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
