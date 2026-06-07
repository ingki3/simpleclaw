"""LLM routing config loader.

LLM provider 설정과 api_key 시크릿 해소를 담당한다.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from simpleclaw.config_sections.common import _resolve_secret_field

# LLM 라우팅 기본 설정값
_LLM_DEFAULTS: dict = {
    "default": "claude",
    "providers": {},
}


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

        # api_key는 참조 문자열일 수 있으므로 항상 시크릿 매니저를 거쳐 해소한다.
        provider["api_key"] = _resolve_secret_field(provider.get("api_key", ""))

        providers[name] = provider

    return {
        "default": llm.get("default", _LLM_DEFAULTS["default"]),
        "providers": providers,
    }

