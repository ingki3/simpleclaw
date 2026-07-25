"""LLM routing config loader.

LLM provider 설정과 api_key 시크릿 해소를 담당한다.
"""

from __future__ import annotations

import copy
import logging
import os
from pathlib import Path

import yaml
from dotenv import dotenv_values

from simpleclaw.config_sections.common import _resolve_secret_field
from simpleclaw.llm.models import LLMConfigError
from simpleclaw.llm.profiles import get_provider_profile, resolve_profile_name
from simpleclaw.llm.transports import resolve_transport_name

logger = logging.getLogger(__name__)

# LLM 라우팅 기본 설정값
# BIZ-448 — fallback/multimodal 은 라우팅 정책 백엔드 이름. None 이면 해당
# 정책이 비활성화되어 기존 default-only 라우팅과 동일하게 동작한다.
_LLM_DEFAULTS: dict = {
    "default": "claude",
    "fallback": None,
    "multimodal": None,
    "routes": {},
    "providers": {},
}


def _clean_optional_str(value: object) -> str | None:
    """Return a stripped string or None for blank/non-string values."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _normalize_provider_identity(name: str, provider: dict) -> tuple[str, str]:
    """Normalize legacy provider config to explicit transport/profile keys."""
    backend_type = provider.get("type", "api")
    explicit_transport = _clean_optional_str(provider.get("transport"))
    explicit_profile = _clean_optional_str(provider.get("profile"))
    legacy_provider = _clean_optional_str(provider.get("provider"))

    if backend_type == "cli":
        transport = explicit_transport or "cli"
        profile = explicit_profile or "generic"
    else:
        profile_source = explicit_profile or legacy_provider or name
        try:
            profile = resolve_profile_name(profile_source)
        except LLMConfigError:
            if explicit_profile:
                raise
            profile = "generic"
        if explicit_transport:
            transport = resolve_transport_name(explicit_transport)
        else:
            try:
                transport = resolve_transport_name(profile_source)
            except LLMConfigError:
                transport = (
                    get_provider_profile(profile).default_transport
                    if profile != "generic"
                    else profile_source
                )

    if legacy_provider and (not explicit_transport or not explicit_profile):
        logger.warning(
            "LLM backend '%s' uses legacy provider=%r; normalized to "
            "transport=%r profile=%r. Prefer explicit transport/profile config.",
            name,
            legacy_provider,
            transport,
            profile,
        )
    elif not explicit_transport and not explicit_profile:
        logger.warning(
            "LLM backend '%s' relies on legacy backend-name provider inference; "
            "normalized to transport=%r profile=%r. Prefer explicit "
            "transport/profile config.",
            name,
            transport,
            profile,
        )
    return transport, profile


def _resolve_provider_api_key(provider: dict, config_dir: Path) -> str:
    """provider의 api_key/api_key_env 설정을 실제 API 키 문자열로 해소한다.

    새 시크릿 참조 문법(``api_key: env:NAME`` 등)을 우선하되, 기존
    scenario/live 설정이 쓰던 ``api_key_env``도 계속 지원한다. ``api_key_env``는
    현재 프로세스 환경변수를 먼저 보고, 없으면 config.yaml 옆의 .env 파일에서
    한 번만 조회한다.
    """
    api_key = _resolve_secret_field(provider.get("api_key", ""))
    if api_key:
        return api_key

    api_key_env = provider.get("api_key_env")
    if not isinstance(api_key_env, str) or not api_key_env:
        return ""

    env_value = os.environ.get(api_key_env)
    if env_value:
        return env_value

    env_file = config_dir / ".env"
    if not env_file.is_file():
        return ""

    dotenv_value = dotenv_values(env_file).get(api_key_env)
    return str(dotenv_value or "")


def _normalize_route(name: str, raw: object) -> dict[str, str | None] | None:
    """Normalize shorthand/full route config without resolving backend availability."""
    if isinstance(raw, str):
        primary = _clean_optional_str(raw)
        retry = None
    elif isinstance(raw, dict):
        primary = _clean_optional_str(raw.get("primary"))
        retry = _clean_optional_str(raw.get("retry"))
    else:
        return None
    if not primary:
        logger.warning("Ignoring LLM route '%s' without a primary backend", name)
        return None
    return {"primary": primary, "retry": retry}


def load_llm_config(config_path: str | Path) -> dict:
    """config.yaml에서 LLM 라우팅 설정을 로드한다.

    각 provider의 ``api_key``는 시크릿 매니저를 통해 해소된다. 참조 문법
    (``"env:ANTHROPIC_API_KEY"``, ``"keyring:claude"``, ``"file:claude"``)을
    권장하며, 평문 키도 하위 호환을 위해 그대로 동작한다.
    파일이 없거나 llm 키가 없으면 기본값을 반환한다.
    """
    config_path = Path(config_path)
    if not config_path.is_file():
        return dict(_LLM_DEFAULTS)

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return dict(_LLM_DEFAULTS)

    if not isinstance(data, dict):
        return dict(_LLM_DEFAULTS)

    llm = data.get("llm", {})
    if not isinstance(llm, dict):
        return dict(_LLM_DEFAULTS)

    providers = {}
    for name, pconfig in llm.get("providers", {}).items():
        if not isinstance(pconfig, dict):
            continue
        provider = dict(pconfig)
        provider["name"] = name
        provider["api_key"] = _resolve_provider_api_key(provider, config_path.parent)
        provider["transport"], provider["profile"] = _normalize_provider_identity(
            name, provider
        )

        providers[name] = provider

    routes: dict[str, dict[str, str | None]] = {}
    # create_router() must distinguish user-authored routes, which fail fast
    # when unavailable, from compatibility routes that may follow an available
    # legacy default after provider initialization.
    route_sources: dict[str, str] = {}
    raw_routes = llm.get("routes", {})
    if isinstance(raw_routes, dict):
        for route_name, raw_route in raw_routes.items():
            if not isinstance(route_name, str) or not route_name.strip():
                continue
            normalized = _normalize_route(route_name.strip(), raw_route)
            if normalized:
                normalized_name = route_name.strip()
                routes[normalized_name] = normalized
                route_sources[normalized_name] = "explicit"

    legacy_default = _clean_optional_str(
        llm.get("default", _LLM_DEFAULTS["default"])
    ) or _LLM_DEFAULTS["default"]
    legacy_fallback = _clean_optional_str(llm.get("fallback"))
    legacy_multimodal = _clean_optional_str(llm.get("multimodal"))
    if "default" not in routes:
        routes["default"] = {"primary": legacy_default, "retry": legacy_fallback}
        route_sources["default"] = "legacy"
    if legacy_multimodal and "multimodal" not in routes:
        routes["multimodal"] = {
            "primary": legacy_multimodal,
            "retry": legacy_fallback,
        }
        route_sources["multimodal"] = "legacy"

    # One-release compatibility: materialize legacy TurnAnalysis model overrides
    # as ordinary static backends, then route to them. This preserves behavior
    # without runtime provider cloning or private-field mutation.
    agent = data.get("agent", {})
    turn_analysis = agent.get("turn_analysis", {}) if isinstance(agent, dict) else {}
    if not isinstance(turn_analysis, dict):
        turn_analysis = {}
    legacy_keys = (
        "backend",
        "provider",
        "model",
        "retry_backend",
        "retry_provider",
        "retry_model",
    )
    explicit_turn_analysis_route = "turn_analysis" in routes
    if not explicit_turn_analysis_route:
        def _legacy_target(prefix: str) -> str | None:
            provider_key = f"{prefix}provider"
            model_key = f"{prefix}model"
            backend_key = f"{prefix}backend"
            provider_name = _clean_optional_str(turn_analysis.get(provider_key))
            model = _clean_optional_str(turn_analysis.get(model_key))
            if provider_name and model and provider_name in providers:
                alias = f"__legacy_turn_analysis_{prefix.rstrip('_') or 'primary'}"
                providers[alias] = copy.deepcopy(providers[provider_name])
                providers[alias]["name"] = alias
                providers[alias]["model"] = model
                return alias
            return _clean_optional_str(turn_analysis.get(backend_key))

        primary = _legacy_target("") or routes["default"]["primary"]
        retry = _legacy_target("retry_") or routes["default"].get("retry")
        routes["turn_analysis"] = {"primary": primary, "retry": retry}
        route_sources["turn_analysis"] = "legacy"

    if any(_clean_optional_str(turn_analysis.get(key)) for key in legacy_keys):
        action = (
            "ignored because explicit llm.routes.turn_analysis takes precedence"
            if explicit_turn_analysis_route
            else "normalized to llm.routes.turn_analysis"
        )
        logger.warning(
            "agent.turn_analysis backend/provider/model selectors are deprecated; "
            "%s. Prefer the route config.",
            action,
        )

    # fallback/multimodal 값 검증(가용 백엔드 존재 여부)은 create_router() 몫 —
    # 여기서는 문자열 또는 None 을 그대로 보존한다.
    return {
        "default": routes["default"]["primary"],
        "fallback": routes["default"].get("retry"),
        "multimodal": routes.get("multimodal", {}).get("primary"),
        "routes": routes,
        "route_sources": route_sources,
        "providers": providers,
    }
