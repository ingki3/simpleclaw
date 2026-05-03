"""SimpleClaw 설정 로더 모듈.

config.yaml 파일에서 각 서브시스템(페르소나, LLM, 데몬, 에이전트, 음성, 텔레그램,
웹훅, 서브 에이전트)의 설정을 로드한다.

설계 결정:
- 각 서브시스템별 독립적인 load_*_config() 함수로 분리
- 파일 누락이나 파싱 오류 시 안전한 기본값(_*_DEFAULTS) 반환
- API 키/토큰 등 민감 정보는 ``simpleclaw.security.secrets``를 통해 OS 자격 증명
  저장소(keyring) 또는 암호화 파일에서 해소한다. config.yaml에는 ``"env:NAME"``,
  ``"keyring:NAME"``, ``"file:NAME"`` 형태의 참조 문자열만 적도록 권장하며,
  레거시 평문 값도 그대로 동작한다(하위 호환).
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from simpleclaw.security.secrets import SecretReference, resolve_secret

logger = logging.getLogger(__name__)


def _resolve_secret_field(value: object) -> str:
    """config.yaml에서 읽은 시크릿 필드 값을 실제 시크릿으로 해소한다.

    - ``None`` 또는 비문자열 → 빈 문자열
    - 참조 문자열(``"env:..."`` 등) → 백엔드에서 조회
    - 평문 → 그대로 반환하되, 비어있지 않으면 보안 경고 로그를 남긴다.
    """
    if not isinstance(value, str) or not value:
        return ""

    ref = SecretReference.parse(value)
    if ref is None:
        # 평문이 들어있으면 마이그레이션을 권장하는 경고를 남긴다 — 한 번 보고
        # 사용자가 인지할 수 있도록 logger.warning으로 발신.
        logger.warning(
            "config.yaml에 평문 시크릿이 감지되었습니다. "
            "보안을 위해 'env:NAME', 'keyring:NAME', 'file:NAME' 참조로 마이그레이션하세요. "
            "(scripts/migrate_secrets.py 참고)"
        )
        return value
    return resolve_secret(value)


# 페르소나 엔진 기본 설정값
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


# 데몬 기본 설정값
_DAEMON_DEFAULTS: dict = {
    "heartbeat_interval": 300,
    "pid_file": ".agent/daemon.pid",
    "status_file": ".agent/HEARTBEAT.md",
    "db_path": ".agent/daemon.db",
    "dreaming": {
        "overnight_hour": 3,
        "idle_threshold": 7200,
        "model": "",
        # Phase 3 그래프형 드리밍 — 기본 False(점진 도입). 켜면 IncrementalClusterer가
        # 미클러스터 임베딩을 부착하고 MEMORY.md의 ``<!-- cluster:N -->`` 마커 영역만 in-place 갱신한다.
        "enable_clusters": False,
        # 클러스터 부착 임계값 — multilingual-e5-small 기준 경험적 컷.
        # 낮추면 클러스터가 커지고(잡음↑), 높이면 작아진다(파편화↑).
        "cluster_threshold": 0.75,
        # BIZ-73: 인사이트 승격 임계 관측 횟수. 단발 관측은 항상 confidence ≤ 0.4 로 캡되고,
        # 이 횟수에 도달해야 승격선(0.7)에 진입한다. 작은 값이면 빨리 승격(잘못된 일반화↑),
        # 큰 값이면 보수적(누적 신뢰성↑). 기본 3회.
        "insight_promotion_threshold": 3,
        # BIZ-78: decay 정책. ``last_seen`` 기준 N일 이상 reinforcement 가 없는 인사이트는
        # archive 처리(USER.md 의 archive 섹션 + sidecar archived_at). null 이면 비활성.
        "decay": {
            "archive_after_days": 30,
        },
        # BIZ-78: reject 차단 리스트. 사용자 거부 신호의 기본 TTL. null 이면 영구.
        # 항목별 override 는 Admin Review Loop(H, BIZ-79) 에서 가능.
        "reject_blocklist": {
            "default_ttl_days": None,
        },
        # BIZ-79: dry-run + admin review 모드. 추출된 인사이트는 USER.md 에 즉시
        # 쓰지 않고 review 큐(.agent/suggestions.jsonl)에 적재된다. auto_promote
        # confidence/evidence_count 를 동시에 충족한 항목만 큐를 우회해 자동 적용.
        "auto_promote_confidence": 0.7,
        "auto_promote_evidence_count": 3,
        # BIZ-80: dreaming 산출물의 1차 언어 정책. ``primary`` 는 USER/MEMORY/AGENT/SOUL
        # dreaming-managed 섹션의 출력 언어 — 기본 "ko" 로 영어 입력에서도 인사이트가
        # 한국어로 통일된다. None 으로 두면 enforcement 없이 LLM 출력을 그대로 통과
        # (BIZ-80 이전 동작). ``min_ratio`` 는 한글/라틴 비율 임계치(0.0~1.0).
        # ``per_file`` 은 파일별 override (예: {"agent": "en"} → AGENT.md 만 영어).
        "language": {
            "primary": "ko",
            "min_ratio": 0.3,
            "per_file": {},
        },
    },
    "wait_state": {
        "default_timeout": 3600,
    },
}


def _coerce_archive_after_days(value: object) -> int | None:
    """archive_after_days 입력을 정규화. 양수만 활성, 그 외(None/0/음수/파싱불가)는 None.

    None 의미: decay 비활성 — apply_decay 가 즉시 noop 으로 종료한다.
    """
    if value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _coerce_default_ttl_days(value: object) -> int | None:
    """reject TTL 기본값 입력을 정규화. 양수만 활성, 그 외는 None(영구 차단)."""
    if value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _coerce_language_policy(raw: dict) -> dict:
    """BIZ-80 — dreaming.language 설정을 정규화한다.

    - ``primary``: 빈 문자열/None 이면 None(=enforcement 비활성). 그 외는 그대로.
      알 수 없는 코드(예: "fr") 도 그대로 통과시킨다 — 휴리스틱이 보수적으로
      통과시키므로 실수로 모든 출력이 잘리는 사고는 일어나지 않는다.
    - ``min_ratio``: float 캐스팅 후 [0.0, 1.0] 으로 클램프. 파싱 실패 시 기본 0.3.
    - ``per_file``: dict[str, str] 만 허용. 그 외는 빈 dict.
    """
    default = _DAEMON_DEFAULTS["dreaming"]["language"]
    primary = raw.get("primary", default["primary"])
    if primary == "" or primary is None:
        primary = None
    else:
        primary = str(primary)

    try:
        min_ratio = float(raw.get("min_ratio", default["min_ratio"]))
    except (TypeError, ValueError):
        min_ratio = float(default["min_ratio"])
    min_ratio = max(0.0, min(1.0, min_ratio))

    per_file_raw = raw.get("per_file", {})
    if isinstance(per_file_raw, dict):
        per_file = {
            str(k): str(v)
            for k, v in per_file_raw.items()
            if isinstance(k, str) and isinstance(v, str) and v
        }
    else:
        per_file = {}

    return {
        "primary": primary,
        "min_ratio": min_ratio,
        "per_file": per_file,
    }


def load_daemon_config(config_path: str | Path) -> dict:
    """config.yaml에서 데몬 설정을 로드한다.

    하트비트 간격, PID/상태 파일 경로, dreaming/wait_state 설정을 포함한다.
    파일이 없거나 daemon 키가 없으면 기본값을 반환한다.
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

    # BIZ-78: dreaming.decay / dreaming.reject_blocklist 는 dict 인 경우만 사용한다.
    # 누락되거나 타입이 깨졌으면 빈 dict 로 떨어뜨려 아래에서 기본값으로 채운다.
    decay = dreaming.get("decay", {})
    if not isinstance(decay, dict):
        decay = {}
    reject = dreaming.get("reject_blocklist", {})
    if not isinstance(reject, dict):
        reject = {}
    # BIZ-80: language 정책. dict 가 아니면 빈 dict 로 떨어뜨려 아래에서 기본값으로 채운다.
    language = dreaming.get("language", {})
    if not isinstance(language, dict):
        language = {}

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
            "model": dreaming.get(
                "model",
                _DAEMON_DEFAULTS["dreaming"]["model"],
            ),
            "enable_clusters": bool(
                dreaming.get(
                    "enable_clusters",
                    _DAEMON_DEFAULTS["dreaming"]["enable_clusters"],
                )
            ),
            "cluster_threshold": float(
                dreaming.get(
                    "cluster_threshold",
                    _DAEMON_DEFAULTS["dreaming"]["cluster_threshold"],
                )
            ),
            "insight_promotion_threshold": max(
                1,
                int(
                    dreaming.get(
                        "insight_promotion_threshold",
                        _DAEMON_DEFAULTS["dreaming"][
                            "insight_promotion_threshold"
                        ],
                    )
                ),
            ),
            # BIZ-78: decay 정책. archive_after_days 가 None/0/음수면 비활성(=archive 단계 skip).
            # 양수만 의미 있는 값 — int 캐스팅 실패 시 기본값으로 fallback.
            "decay": {
                "archive_after_days": _coerce_archive_after_days(
                    decay.get(
                        "archive_after_days",
                        _DAEMON_DEFAULTS["dreaming"]["decay"][
                            "archive_after_days"
                        ],
                    )
                ),
            },
            # BIZ-78: reject 차단 리스트 기본 TTL(일). None/0/음수면 영구 차단(현재 흔한 케이스).
            "reject_blocklist": {
                "default_ttl_days": _coerce_default_ttl_days(
                    reject.get(
                        "default_ttl_days",
                        _DAEMON_DEFAULTS["dreaming"]["reject_blocklist"][
                            "default_ttl_days"
                        ],
                    )
                ),
            },
            # BIZ-79: dry-run + admin review 모드.
            "auto_promote_confidence": float(
                dreaming.get(
                    "auto_promote_confidence",
                    _DAEMON_DEFAULTS["dreaming"]["auto_promote_confidence"],
                )
            ),
            "auto_promote_evidence_count": max(
                1,
                int(
                    dreaming.get(
                        "auto_promote_evidence_count",
                        _DAEMON_DEFAULTS["dreaming"][
                            "auto_promote_evidence_count"
                        ],
                    )
                ),
            ),
            # BIZ-80: language 정책. ``primary=None`` 이면 enforcement 비활성, 그 외
            # 코드("ko"/"en") 면 dreaming 출력을 해당 언어로 강제. ``min_ratio`` 는
            # 0.0~1.0 으로 클램프. ``per_file`` 은 파일 식별자→언어 코드 dict 만 허용.
            "language": _coerce_language_policy(language),
        },
        "wait_state": {
            "default_timeout": wait_state.get(
                "default_timeout",
                _DAEMON_DEFAULTS["wait_state"]["default_timeout"],
            ),
        },
    }


# 에이전트 오케스트레이터 기본 설정값
_AGENT_DEFAULTS: dict = {
    "history_limit": 20,
    "db_path": ".agent/conversations.db",
    "max_tool_iterations": 5,
    "workspace_dir": ".agent/workspace",
}


def load_agent_config(config_path: str | Path) -> dict:
    """config.yaml에서 에이전트 오케스트레이터 설정을 로드한다."""
    config_path = Path(config_path)
    if not config_path.is_file():
        return dict(_AGENT_DEFAULTS)

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return dict(_AGENT_DEFAULTS)

    if not isinstance(data, dict):
        return dict(_AGENT_DEFAULTS)

    agent = data.get("agent", {})
    if not isinstance(agent, dict):
        return dict(_AGENT_DEFAULTS)

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
    }


# 시맨틱 메모리(RAG) 기본 설정값
# 모든 키는 안전한 기본값을 가진다 — config.yaml에 memory 섹션이 없어도 봇은 동작한다.
# enabled=False가 기본인 이유: sentence-transformers는 무거운 의존성이라
# 사용자가 명시적으로 켜야 한다(첫 인코딩 시 모델 다운로드 ~500MB 발생).
_MEMORY_DEFAULTS: dict = {
    "rag": {
        "enabled": False,
        "model": "intfloat/multilingual-e5-small",
        "top_k": 5,
        "similarity_threshold": 0.5,
    },
}


def load_memory_config(config_path: str | Path) -> dict:
    """config.yaml에서 시맨틱 메모리(RAG) 설정을 로드한다.

    파일이 없거나 memory 키가 없으면 기본값(RAG 비활성)을 반환한다.
    """
    config_path = Path(config_path)
    if not config_path.is_file():
        return {"rag": dict(_MEMORY_DEFAULTS["rag"])}

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return {"rag": dict(_MEMORY_DEFAULTS["rag"])}

    if not isinstance(data, dict):
        return {"rag": dict(_MEMORY_DEFAULTS["rag"])}

    memory = data.get("memory", {})
    if not isinstance(memory, dict):
        return {"rag": dict(_MEMORY_DEFAULTS["rag"])}

    rag = memory.get("rag", {})
    if not isinstance(rag, dict):
        rag = {}

    return {
        "rag": {
            "enabled": rag.get("enabled", _MEMORY_DEFAULTS["rag"]["enabled"]),
            "model": rag.get("model", _MEMORY_DEFAULTS["rag"]["model"]),
            "top_k": rag.get("top_k", _MEMORY_DEFAULTS["rag"]["top_k"]),
            "similarity_threshold": rag.get(
                "similarity_threshold",
                _MEMORY_DEFAULTS["rag"]["similarity_threshold"],
            ),
        },
    }


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
_TELEGRAM_DEFAULTS: dict = {
    "bot_token": "",
    "whitelist": {
        "user_ids": [],
        "chat_ids": [],
    },
}


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
