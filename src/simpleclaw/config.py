"""Configuration loader for SimpleClaw."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv


_DEFAULTS = {
    "token_budget": 4096,
    "local_dir": ".agent",
    "global_dir": "~/.agents/main",
    "files": [
        {"name": "AGENT.md", "type": "agent"},
        {"name": "USER.md", "type": "user"},
        {"name": "MEMORY.md", "type": "memory"},
    ],
}


def load_persona_config(config_path: str | Path) -> dict:
    """Load persona engine configuration from config.yaml.

    Returns defaults if the file or persona key is missing.
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


_LLM_DEFAULTS: dict = {
    "default": "claude",
    "providers": {},
}


def load_llm_config(config_path: str | Path) -> dict:
    """Load LLM routing configuration from config.yaml.

    Also loads .env file for API keys.
    Returns defaults if the file or llm key is missing.
    """
    # Load .env for API keys
    env_path = Path(config_path).parent / ".env"
    if env_path.is_file():
        load_dotenv(env_path)

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

    # Resolve API keys from environment variables
    providers = {}
    for name, pconfig in llm.get("providers", {}).items():
        if not isinstance(pconfig, dict):
            continue
        provider = dict(pconfig)
        provider["name"] = name

        # Resolve API key from env var
        api_key_env = provider.get("api_key_env")
        if api_key_env:
            provider["api_key"] = os.environ.get(api_key_env, "")

        providers[name] = provider

    return {
        "default": llm.get("default", _LLM_DEFAULTS["default"]),
        "providers": providers,
    }
