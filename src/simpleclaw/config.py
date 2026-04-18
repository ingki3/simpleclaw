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


_DAEMON_DEFAULTS: dict = {
    "heartbeat_interval": 300,
    "pid_file": ".agent/daemon.pid",
    "status_file": ".agent/HEARTBEAT.md",
    "db_path": ".agent/daemon.db",
    "dreaming": {
        "overnight_hour": 3,
        "idle_threshold": 7200,
    },
    "wait_state": {
        "default_timeout": 3600,
    },
}


def load_daemon_config(config_path: str | Path) -> dict:
    """Load daemon configuration from config.yaml.

    Returns defaults if the file or daemon key is missing.
    """
    config_path = Path(config_path)
    if not config_path.is_file():
        return dict(_DAEMON_DEFAULTS)

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return dict(_DAEMON_DEFAULTS)

    if not isinstance(data, dict):
        return dict(_DAEMON_DEFAULTS)

    daemon = data.get("daemon", {})
    if not isinstance(daemon, dict):
        return dict(_DAEMON_DEFAULTS)

    dreaming = daemon.get("dreaming", {})
    if not isinstance(dreaming, dict):
        dreaming = {}

    wait_state = daemon.get("wait_state", {})
    if not isinstance(wait_state, dict):
        wait_state = {}

    return {
        "heartbeat_interval": daemon.get(
            "heartbeat_interval", _DAEMON_DEFAULTS["heartbeat_interval"]
        ),
        "pid_file": daemon.get("pid_file", _DAEMON_DEFAULTS["pid_file"]),
        "status_file": daemon.get(
            "status_file", _DAEMON_DEFAULTS["status_file"]
        ),
        "db_path": daemon.get("db_path", _DAEMON_DEFAULTS["db_path"]),
        "dreaming": {
            "overnight_hour": dreaming.get(
                "overnight_hour",
                _DAEMON_DEFAULTS["dreaming"]["overnight_hour"],
            ),
            "idle_threshold": dreaming.get(
                "idle_threshold",
                _DAEMON_DEFAULTS["dreaming"]["idle_threshold"],
            ),
        },
        "wait_state": {
            "default_timeout": wait_state.get(
                "default_timeout",
                _DAEMON_DEFAULTS["wait_state"]["default_timeout"],
            ),
        },
    }


_VOICE_DEFAULTS: dict = {
    "stt": {
        "provider": "openai",
        "model": "whisper-1",
        "max_duration": 300,
    },
    "tts": {
        "provider": "openai",
        "model": "tts-1",
        "voice": "alloy",
        "speed": 1.0,
        "output_format": "mp3",
        "max_text_length": 4096,
    },
}


def load_voice_config(config_path: str | Path) -> dict:
    """Load voice (STT/TTS) configuration from config.yaml."""
    config_path = Path(config_path)
    if not config_path.is_file():
        return dict(_VOICE_DEFAULTS)

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return dict(_VOICE_DEFAULTS)

    if not isinstance(data, dict):
        return dict(_VOICE_DEFAULTS)

    voice = data.get("voice", {})
    if not isinstance(voice, dict):
        return dict(_VOICE_DEFAULTS)

    stt = voice.get("stt", {})
    if not isinstance(stt, dict):
        stt = {}

    tts = voice.get("tts", {})
    if not isinstance(tts, dict):
        tts = {}

    return {
        "stt": {
            "provider": stt.get("provider", _VOICE_DEFAULTS["stt"]["provider"]),
            "model": stt.get("model", _VOICE_DEFAULTS["stt"]["model"]),
            "max_duration": stt.get(
                "max_duration", _VOICE_DEFAULTS["stt"]["max_duration"]
            ),
        },
        "tts": {
            "provider": tts.get("provider", _VOICE_DEFAULTS["tts"]["provider"]),
            "model": tts.get("model", _VOICE_DEFAULTS["tts"]["model"]),
            "voice": tts.get("voice", _VOICE_DEFAULTS["tts"]["voice"]),
            "speed": tts.get("speed", _VOICE_DEFAULTS["tts"]["speed"]),
            "output_format": tts.get(
                "output_format", _VOICE_DEFAULTS["tts"]["output_format"]
            ),
            "max_text_length": tts.get(
                "max_text_length", _VOICE_DEFAULTS["tts"]["max_text_length"]
            ),
        },
    }


_TELEGRAM_DEFAULTS: dict = {
    "bot_token_env": "TELEGRAM_BOT_TOKEN",
    "whitelist": {
        "user_ids": [],
        "chat_ids": [],
    },
}


def load_telegram_config(config_path: str | Path) -> dict:
    """Load Telegram bot configuration from config.yaml."""
    config_path = Path(config_path)
    if not config_path.is_file():
        return dict(_TELEGRAM_DEFAULTS)

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return dict(_TELEGRAM_DEFAULTS)

    if not isinstance(data, dict):
        return dict(_TELEGRAM_DEFAULTS)

    tg = data.get("telegram", {})
    if not isinstance(tg, dict):
        return dict(_TELEGRAM_DEFAULTS)

    whitelist = tg.get("whitelist", {})
    if not isinstance(whitelist, dict):
        whitelist = {}

    # Load .env for bot token
    env_path = Path(config_path).parent / ".env"
    if env_path.is_file():
        load_dotenv(env_path)

    token_env = tg.get("bot_token_env", _TELEGRAM_DEFAULTS["bot_token_env"])
    bot_token = os.environ.get(token_env, "")

    return {
        "bot_token": bot_token,
        "bot_token_env": token_env,
        "whitelist": {
            "user_ids": whitelist.get("user_ids", []),
            "chat_ids": whitelist.get("chat_ids", []),
        },
    }


_WEBHOOK_DEFAULTS: dict = {
    "enabled": True,
    "host": "127.0.0.1",
    "port": 8080,
    "auth_token_env": "WEBHOOK_AUTH_TOKEN",
}


def load_webhook_config(config_path: str | Path) -> dict:
    """Load webhook server configuration from config.yaml."""
    config_path = Path(config_path)
    if not config_path.is_file():
        return dict(_WEBHOOK_DEFAULTS)

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return dict(_WEBHOOK_DEFAULTS)

    if not isinstance(data, dict):
        return dict(_WEBHOOK_DEFAULTS)

    wh = data.get("webhook", {})
    if not isinstance(wh, dict):
        return dict(_WEBHOOK_DEFAULTS)

    # Load .env for auth token
    env_path = Path(config_path).parent / ".env"
    if env_path.is_file():
        load_dotenv(env_path)

    token_env = wh.get("auth_token_env", _WEBHOOK_DEFAULTS["auth_token_env"])
    auth_token = os.environ.get(token_env, "")

    return {
        "enabled": wh.get("enabled", _WEBHOOK_DEFAULTS["enabled"]),
        "host": wh.get("host", _WEBHOOK_DEFAULTS["host"]),
        "port": wh.get("port", _WEBHOOK_DEFAULTS["port"]),
        "auth_token": auth_token,
        "auth_token_env": token_env,
    }


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


def load_sub_agents_config(config_path: str | Path) -> dict:
    """Load sub-agents configuration from config.yaml."""
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
