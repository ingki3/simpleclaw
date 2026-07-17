"""LLM routing config loader.

LLM provider 설정과 api_key 시크릿 해소를 담당한다.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values
import yaml

from simpleclaw.config_sections.common import _resolve_secret_field

# LLM 라우팅 기본 설정값
# BIZ-448 — fallback/multimodal 은 라우팅 정책 백엔드 이름. None 이면 해당
# 정책이 비활성화되어 기존 default-only 라우팅과 동일하게 동작한다.
_LLM_DEFAULTS: dict = {
    "default": "claude",
    "fallback": None,
    "multimodal": None,
    "providers": {},
}


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

        providers[name] = provider

    # fallback/multimodal 값 검증(가용 백엔드 존재 여부)은 create_router() 몫 —
    # 여기서는 문자열 또는 None 을 그대로 보존한다.
    return {
        "default": llm.get("default", _LLM_DEFAULTS["default"]),
        "fallback": llm.get("fallback", _LLM_DEFAULTS["fallback"]),
        "multimodal": llm.get("multimodal", _LLM_DEFAULTS["multimodal"]),
        "providers": providers,
    }
