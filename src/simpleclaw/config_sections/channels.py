"""Voice, Telegram, webhook, Admin API config loaders.

외부 채널과 로컬 Admin API 설정을 담당하며 토큰류는 common 시크릿 해소를 사용한다.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from simpleclaw.config_sections.common import _resolve_secret_field

# 음성(STT/TTS) 기본 설정값
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
    """config.yaml에서 음성(STT/TTS) 설정을 로드한다."""
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


# 텔레그램 봇 기본 설정값
# BIZ-259 — streaming 기본값: 끄고(enabled=False) 들어와 옵트인. 봇 재기동만으로
# 회귀가 되지 않도록 보수적 기본을 둔다. 운영자가 환경별 튜닝 후 켠다.
_TELEGRAM_STREAMING_DEFAULTS: dict = {
    "enabled": False,
    "min_interval_ms": 800,
    "min_delta_chars": 40,
    "initial_placeholder": "…",
    "final_only_for_cron": True,
    "tool_progress": True,
}

_TELEGRAM_DEFAULTS: dict = {
    "bot_token": "",
    "whitelist": {
        "user_ids": [],
        "chat_ids": [],
    },
    "streaming": dict(_TELEGRAM_STREAMING_DEFAULTS),
    "buttons": {
        "enabled": True,
    },
}


def _coerce_streaming_config(raw: object) -> dict:
    """``telegram.streaming`` 서브블록을 기본값으로 채워 정규화한다.

    누락 키는 ``_TELEGRAM_STREAMING_DEFAULTS`` 로 보강하고, 잘못된 타입은
    기본값으로 복원한다. raw 가 dict 가 아니면 (또는 None 이면) 기본값 전체.
    """
    merged = dict(_TELEGRAM_STREAMING_DEFAULTS)
    if not isinstance(raw, dict):
        return merged

    try:
        merged["enabled"] = bool(raw.get("enabled", merged["enabled"]))
    except (TypeError, ValueError):
        pass
    try:
        merged["min_interval_ms"] = int(
            raw.get("min_interval_ms", merged["min_interval_ms"])
        )
    except (TypeError, ValueError):
        pass
    try:
        merged["min_delta_chars"] = int(
            raw.get("min_delta_chars", merged["min_delta_chars"])
        )
    except (TypeError, ValueError):
        pass
    placeholder = raw.get("initial_placeholder", merged["initial_placeholder"])
    if isinstance(placeholder, str) and placeholder:
        merged["initial_placeholder"] = placeholder
    try:
        merged["final_only_for_cron"] = bool(
            raw.get("final_only_for_cron", merged["final_only_for_cron"])
        )
    except (TypeError, ValueError):
        pass
    try:
        merged["tool_progress"] = bool(
            raw.get("tool_progress", merged["tool_progress"])
        )
    except (TypeError, ValueError):
        pass
    return merged


def load_telegram_config(config_path: str | Path) -> dict:
    """config.yaml에서 텔레그램 봇 설정을 로드한다."""
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

    # bot_token은 참조 문자열(예: "keyring:telegram_bot_token")을 통해 해소한다.
    bot_token = _resolve_secret_field(tg.get("bot_token", ""))

    return {
        "bot_token": bot_token,
        "whitelist": {
            "user_ids": whitelist.get("user_ids", []),
            "chat_ids": whitelist.get("chat_ids", []),
        },
        "streaming": _coerce_streaming_config(tg.get("streaming")),
        "buttons": {
            "enabled": bool(
                (tg.get("buttons", {}) or {}).get(
                    "enabled", _TELEGRAM_DEFAULTS["buttons"]["enabled"]
                )
                if isinstance(tg.get("buttons", {}), dict)
                else _TELEGRAM_DEFAULTS["buttons"]["enabled"]
            ),
        },
    }


# 웹훅 서버 기본 설정값
# BIZ-24: max_body_size/rate_limit/concurrency 기본값은 공개 엔드포인트에 노출돼도
# 일반적인 트래픽은 막지 않으면서 명백한 학대성 호출을 차단할 수 있도록 보수적으로 설정.
_WEBHOOK_DEFAULTS: dict = {
    "enabled": True,
    "host": "127.0.0.1",
    "port": 8080,
    "auth_token": "",
    "max_body_size": 1_048_576,  # 1MB
    "rate_limit": 60,             # 윈도우당 요청 수 (0이면 비활성)
    "rate_limit_window": 60.0,    # 슬라이딩 윈도우(초)
    "max_concurrent_connections": 32,
    "queue_size": 64,             # 동시성 cap 초과 시 대기 가능한 요청 수
    "alert_cooldown": 300.0,      # 동일 알림 키의 최소 발신 간격(초)
}


def load_webhook_config(config_path: str | Path) -> dict:
    """config.yaml에서 웹훅 서버 설정을 로드한다.

    BIZ-24: 페이로드 크기 상한, 슬라이딩 윈도우 rate limit, 동시성 cap, 알림 쿨다운
    설정을 읽어들이며, 누락된 키는 보안 기본값으로 채운다.
    """
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

    # auth_token도 참조 문자열을 지원 — 평문 토큰을 config에서 분리.
    auth_token = _resolve_secret_field(wh.get("auth_token", ""))

    return {
        "enabled": wh.get("enabled", _WEBHOOK_DEFAULTS["enabled"]),
        "host": wh.get("host", _WEBHOOK_DEFAULTS["host"]),
        "port": wh.get("port", _WEBHOOK_DEFAULTS["port"]),
        "auth_token": auth_token,
        "max_body_size": int(
            wh.get("max_body_size", _WEBHOOK_DEFAULTS["max_body_size"])
        ),
        "rate_limit": int(
            wh.get("rate_limit", _WEBHOOK_DEFAULTS["rate_limit"])
        ),
        "rate_limit_window": float(
            wh.get("rate_limit_window", _WEBHOOK_DEFAULTS["rate_limit_window"])
        ),
        "max_concurrent_connections": int(
            wh.get(
                "max_concurrent_connections",
                _WEBHOOK_DEFAULTS["max_concurrent_connections"],
            )
        ),
        "queue_size": int(
            wh.get("queue_size", _WEBHOOK_DEFAULTS["queue_size"])
        ),
        "alert_cooldown": float(
            wh.get("alert_cooldown", _WEBHOOK_DEFAULTS["alert_cooldown"])
        ),
    }


# Admin API 서버 기본 설정값 (BIZ-58)
# 단일 운영자 가정의 로컬 백오피스 API. enabled=True가 기본이지만 토큰이 없으면
# 부팅 단계에서 명시적으로 실패하여 silent insecure 운용을 방지한다.
# bind_host는 ``127.0.0.1`` 고정 권장 — 외부 노출 시 mTLS 등 추가 가드가 필요하다.
_ADMIN_API_DEFAULTS: dict = {
    "enabled": True,
    "bind_host": "127.0.0.1",
    "bind_port": 8082,
    # 시크릿 참조 권장: ``"keyring:admin_api_token"`` 등.
    "token_secret": "keyring:admin_api_token",
    "read_timeout_seconds": 30,
    # 256 KiB — 설정 PATCH 페이로드 상한. yaml 한 영역 머지에 충분.
    "request_max_body_kb": 256,
    # CORS 허용 origin 목록 — Admin UI dev 서버 등. 빈 리스트면 CORS 헤더 미부착(=동일 origin만).
    "cors_origins": [],
}


def load_admin_api_config(config_path: str | Path) -> dict:
    """config.yaml에서 Admin API 서버 설정을 로드한다.

    ``token_secret``은 시크릿 매니저를 통해 해소된다 — keyring/file/env 어디든 가능.
    파일이 없거나 admin_api 키가 없으면 기본값을 반환한다(여전히 enabled=True인 점에
    주의 — 토큰이 비어 있으면 호출자가 부팅 단계에서 명시적으로 실패해야 한다).
    """
    config_path = Path(config_path)
    if not config_path.is_file():
        return _admin_api_with_defaults({})

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return _admin_api_with_defaults({})

    if not isinstance(data, dict):
        return _admin_api_with_defaults({})

    admin = data.get("admin_api", {})
    if not isinstance(admin, dict):
        admin = {}
    return _admin_api_with_defaults(admin)


def _admin_api_with_defaults(admin: dict) -> dict:
    """Admin API 설정 dict를 기본값으로 보강하고 시크릿 참조를 해소해 반환."""
    cors = admin.get("cors_origins", _ADMIN_API_DEFAULTS["cors_origins"])
    if not isinstance(cors, list):
        cors = []
    # token_secret은 참조 문자열일 수 있으므로 항상 시크릿 매니저를 거쳐 해소한다.
    token = _resolve_secret_field(
        admin.get("token_secret", _ADMIN_API_DEFAULTS["token_secret"])
    )
    return {
        "enabled": bool(admin.get("enabled", _ADMIN_API_DEFAULTS["enabled"])),
        "bind_host": admin.get("bind_host", _ADMIN_API_DEFAULTS["bind_host"]),
        "bind_port": int(admin.get("bind_port", _ADMIN_API_DEFAULTS["bind_port"])),
        "token_secret": token,
        "read_timeout_seconds": int(
            admin.get(
                "read_timeout_seconds",
                _ADMIN_API_DEFAULTS["read_timeout_seconds"],
            )
        ),
        "request_max_body_kb": int(
            admin.get(
                "request_max_body_kb",
                _ADMIN_API_DEFAULTS["request_max_body_kb"],
            )
        ),
        "cors_origins": [str(o) for o in cors],
    }
