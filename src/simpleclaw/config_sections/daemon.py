"""Daemon and dreaming config loader.

heartbeat, wait_state, dreaming, proactive 정책 coercion을 담당한다.
"""

from __future__ import annotations

from pathlib import Path

import yaml

# 데몬 기본 설정값
# BIZ-302/313: 운영 데이터(.pid/.db/HEARTBEAT.md)는 배포 repo(`~/.simpleclaw`)
# 와 분리된 런타임 루트(`~/.simpleclaw-agent/default`) 아래에 둔다. 저장소
# working tree 안에는 dreaming 런타임이 쓰는 라이브 파일이 더 이상 존재하지 않게 한다.
_DAEMON_DEFAULTS: dict = {
    "heartbeat_interval": 300,
    "pid_file": "~/.simpleclaw-agent/default/daemon.pid",
    "status_file": "~/.simpleclaw-agent/default/HEARTBEAT.md",
    "db_path": "~/.simpleclaw-agent/default/daemon.db",
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
        # 쓰지 않고 review 큐(suggestions.jsonl)에 적재된다. auto_promote
        # confidence/evidence_count 를 동시에 충족한 항목만 큐를 우회해 자동 적용.
        "auto_promote_confidence": 0.7,
        "auto_promote_evidence_count": 3,
        # BIZ-302/313: dreaming 사이드카 파일 경로 — 런타임 디렉터리
        # (`~/.simpleclaw-agent/default/`)
        # 외부 이전을 위해 코드 하드코드를 제거하고 모두 config 로 빼낸다.
        # 운영자가 다른 위치에 두고 싶다면 config.yaml 에서 개별적으로 override 가능.
        "insights_file": "~/.simpleclaw-agent/default/insights.jsonl",
        "suggestions_file": "~/.simpleclaw-agent/default/suggestions.jsonl",
        "blocklist_file": "~/.simpleclaw-agent/default/insight_blocklist.jsonl",
        "runs_file": "~/.simpleclaw-agent/default/dreaming_runs.jsonl",
        # BIZ-132 (Phase 1+2) / BIZ-133 — safety_backup 디렉터리. dreaming 사이클
        # 직전 라이브 파일을 통째로 스냅샷해 두는 위치. 운영 데이터와 같은 루트
        # 아래 두어 백업/복원이 동일 마운트 내에서 일어나도록 한다.
        "safety_backup_dir": "~/.simpleclaw-agent/default/_safety_backup",
        # BIZ-74 / BIZ-133: Active Projects 패널 sidecar. enabled=True 가 기본,
        # window_days 는 USER.md 의 active-projects 섹션에 노출할 최근성 윈도우.
        "active_projects": {
            "enabled": True,
            "window_days": 7,
            "sidecar_path": "~/.simpleclaw-agent/default/active_projects.jsonl",
        },
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
        # BIZ-297 (parent BIZ-296): 파일별 dreaming 출력 토큰 cap. BIZ-299 의 파일별
        # 분리 dreaming 이 각 호출에서 해당 키를 ``LLMRequest.max_tokens`` 로 사용한다.
        # 값이 None / 0 / 음수면 fallback (프로바이더 기본값) — 0 으로 cap 을 거는
        # 실수는 의미 있는 응답을 거의 보장 못 하므로 None 으로 떨어뜨려 회귀 0.
        # ``cluster`` 는 BIZ-299 의 cluster summary 호출용 — 단일 클러스터 요약은
        # USER 류 인사이트보다 짧게 잘려도 손실이 적어 1024 로 둔다.
        "max_tokens": {
            "memory": 2048,
            "user": 1024,
            "soul": 512,
            "agent": 512,
            "cluster": 1024,
        },
    },
    "wait_state": {
        "default_timeout": 3600,
    },
    # BIZ-442: LaunchAgent restart drain/quiesce. state_file 은 deploy script 와
    # bot 프로세스가 공유하는 drain 요청 파일, default_timeout 은 drain 요청의
    # 자동 만료 시간(초) — script 가 clear 없이 죽어도 이 시간 뒤 intake 가
    # 정상 복귀한다.
    "drain": {
        "state_file": "~/.simpleclaw-agent/default/drain_state.json",
        "default_timeout": 120,
    },
    # BIZ-332: proactive 후보는 fail-closed/low-noise 기본값으로만 활성화된다.
    "proactive": {
        "enabled": False,
        "mode": "low",
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "max_messages_per_day": 1,
        "topic_cooldown_days": 14,
        "dismissed_cooldown_days": 30,
        "min_confidence": 0.75,
        "store_file": "~/.simpleclaw-agent/default/proactive_opportunities.jsonl",
        "presenter": {
            "enabled": False,
            "interval_minutes": 30,
        },
        "actions": {
            "create_cron": {
                "enabled": False,
            },
        },
        "extractors": {
            "dreaming": {
                "enabled": False,
                "lookback_days": 14,
                "repeated_task": {
                    "min_occurrences": 5,
                    "time_bucket_hours": 2,
                },
                "interest_based": {
                    "enabled": False,
                },
                "context_cron": {
                    "enabled": False,
                    "conversation_lookback_hours": 24,
                    "calendar_lookahead_hours": 24,
                    "mail_lookback_hours": 24,
                    "mail_query": "in:inbox newer_than:1d",
                    "require_user_approval": True,
                    "max_context_opportunities_per_run": 3,
                    "allow_recurring": True,
                    "allow_one_shot": True,
                    "calendar_skill_path": "~/.agents/skills/google-calendar-skill",
                    "gmail_skill_path": "~/.agents/skills/gmail-skill",
                },
            },
            "conversation_end": {
                "enabled": False,
                "max_latency_ms": 50,
            },
        },
        "event_hooks": {
            "enabled": False,
            "cron_failure": {
                "enabled": False,
            },
        },
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


def _coerce_dreaming_max_tokens(raw: object) -> dict:
    """BIZ-297 — ``dreaming.max_tokens`` 입력을 정규화한다.

    각 파일 키(memory/user/soul/agent/cluster) 별로 양수 int 만 유효값으로
    인정한다. None / 0 / 음수 / 비-수치 입력은 fallback 으로 None 으로 떨어뜨려
    프로바이더 기본값(예: Claude 4096) 으로 회귀 — 운영자가 부주의하게 0 을
    박아 출력이 잘리는 사고를 피한다. 누락된 키는 ``_DAEMON_DEFAULTS`` 의
    추천값으로 채운다.
    """
    defaults = _DAEMON_DEFAULTS["dreaming"]["max_tokens"]
    merged: dict[str, int | None] = {}
    if not isinstance(raw, dict):
        return dict(defaults)
    for key, default_val in defaults.items():
        value = raw.get(key, default_val)
        if value is None:
            merged[key] = None
            continue
        try:
            n = int(value)
        except (TypeError, ValueError):
            merged[key] = default_val
            continue
        merged[key] = n if n > 0 else None
    return merged


def _positive_int(value: object, default: int) -> int:
    """양수 정수 설정만 받아들이고 깨진 값은 기본값으로 되돌린다."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def _clamped_float(value: object, default: float, *, lower: float, upper: float) -> float:
    """float 설정을 지정 구간으로 클램프해 정책 임계값 실수를 줄인다."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(lower, min(upper, parsed))


def _coerce_context_cron(raw: object, defaults: dict) -> dict:
    """Dreaming context-aware cron 설정을 fail-closed 기본값과 병합한다."""
    if not isinstance(raw, dict):
        raw = {}
    return {
        "enabled": bool(raw.get("enabled", defaults["enabled"])),
        "conversation_lookback_hours": _positive_int(raw.get("conversation_lookback_hours", defaults["conversation_lookback_hours"]), defaults["conversation_lookback_hours"]),
        "calendar_lookahead_hours": _positive_int(raw.get("calendar_lookahead_hours", defaults["calendar_lookahead_hours"]), defaults["calendar_lookahead_hours"]),
        "mail_lookback_hours": _positive_int(raw.get("mail_lookback_hours", defaults["mail_lookback_hours"]), defaults["mail_lookback_hours"]),
        "mail_query": str(raw.get("mail_query", defaults["mail_query"])),
        "require_user_approval": bool(raw.get("require_user_approval", defaults["require_user_approval"])),
        "max_context_opportunities_per_run": _positive_int(raw.get("max_context_opportunities_per_run", defaults["max_context_opportunities_per_run"]), defaults["max_context_opportunities_per_run"]),
        "allow_recurring": bool(raw.get("allow_recurring", defaults["allow_recurring"])),
        "allow_one_shot": bool(raw.get("allow_one_shot", defaults["allow_one_shot"])),
        "calendar_skill_path": str(raw.get("calendar_skill_path", defaults["calendar_skill_path"])),
        "gmail_skill_path": str(raw.get("gmail_skill_path", defaults["gmail_skill_path"])),
    }


def _coerce_proactive_policy(raw: object) -> dict:
    """daemon.proactive 설정을 fail-closed 기본값과 병합한다."""
    defaults = _DAEMON_DEFAULTS["proactive"]
    if not isinstance(raw, dict):
        raw = {}
    quiet = raw.get("quiet_hours", {})
    if not isinstance(quiet, dict):
        quiet = {}
    mode = str(raw.get("mode", defaults["mode"]) or defaults["mode"]).lower()
    if mode not in {"off", "low", "normal", "high"}:
        mode = defaults["mode"]
    extractors = raw.get("extractors", {})
    if not isinstance(extractors, dict):
        extractors = {}
    dreaming_raw = extractors.get("dreaming", {})
    if not isinstance(dreaming_raw, dict):
        dreaming_raw = {}
    dreaming_defaults = defaults["extractors"]["dreaming"]
    repeated_raw = dreaming_raw.get("repeated_task", {})
    if not isinstance(repeated_raw, dict):
        repeated_raw = {}
    interest_raw = dreaming_raw.get("interest_based", {})
    if not isinstance(interest_raw, dict):
        interest_raw = {}
    context_cron_raw = dreaming_raw.get("context_cron", {})
    if not isinstance(context_cron_raw, dict):
        context_cron_raw = {}
    conversation_raw = extractors.get("conversation_end", {})
    if not isinstance(conversation_raw, dict):
        conversation_raw = {}
    conversation_defaults = defaults["extractors"]["conversation_end"]
    event_hooks_raw = raw.get("event_hooks", {})
    if not isinstance(event_hooks_raw, dict):
        event_hooks_raw = {}
    event_hooks_defaults = defaults["event_hooks"]
    cron_failure_raw = event_hooks_raw.get("cron_failure", {})
    if not isinstance(cron_failure_raw, dict):
        cron_failure_raw = {}
    presenter_raw = raw.get("presenter", {})
    if not isinstance(presenter_raw, dict):
        presenter_raw = {}
    presenter_defaults = defaults["presenter"]
    actions_raw = raw.get("actions", {})
    if not isinstance(actions_raw, dict):
        actions_raw = {}
    create_cron_raw = actions_raw.get("create_cron", {})
    if not isinstance(create_cron_raw, dict):
        create_cron_raw = {}
    actions_defaults = defaults["actions"]

    return {
        "enabled": bool(raw.get("enabled", defaults["enabled"])),
        "mode": mode,
        "quiet_hours": {
            "start": str(quiet.get("start", defaults["quiet_hours"]["start"])),
            "end": str(quiet.get("end", defaults["quiet_hours"]["end"])),
        },
        "max_messages_per_day": _positive_int(
            raw.get("max_messages_per_day", defaults["max_messages_per_day"]),
            defaults["max_messages_per_day"],
        ),
        "topic_cooldown_days": _positive_int(
            raw.get("topic_cooldown_days", defaults["topic_cooldown_days"]),
            defaults["topic_cooldown_days"],
        ),
        "dismissed_cooldown_days": _positive_int(
            raw.get("dismissed_cooldown_days", defaults["dismissed_cooldown_days"]),
            defaults["dismissed_cooldown_days"],
        ),
        "min_confidence": _clamped_float(
            raw.get("min_confidence", defaults["min_confidence"]),
            defaults["min_confidence"],
            lower=0.0,
            upper=1.0,
        ),
        "store_file": str(raw.get("store_file", defaults["store_file"])),
        "presenter": {
            "enabled": bool(presenter_raw.get("enabled", presenter_defaults["enabled"])),
            "interval_minutes": _positive_int(
                presenter_raw.get("interval_minutes", presenter_defaults["interval_minutes"]),
                presenter_defaults["interval_minutes"],
            ),
        },
        "actions": {
            "create_cron": {
                "enabled": bool(create_cron_raw.get(
                    "enabled",
                    actions_defaults["create_cron"]["enabled"],
                )),
            },
        },
        "extractors": {
            "dreaming": {
                "enabled": bool(dreaming_raw.get("enabled", dreaming_defaults["enabled"])),
                "lookback_days": _positive_int(
                    dreaming_raw.get("lookback_days", dreaming_defaults["lookback_days"]),
                    dreaming_defaults["lookback_days"],
                ),
                "repeated_task": {
                    "min_occurrences": _positive_int(
                        repeated_raw.get(
                            "min_occurrences",
                            dreaming_defaults["repeated_task"]["min_occurrences"],
                        ),
                        dreaming_defaults["repeated_task"]["min_occurrences"],
                    ),
                    "time_bucket_hours": _positive_int(
                        repeated_raw.get(
                            "time_bucket_hours",
                            dreaming_defaults["repeated_task"]["time_bucket_hours"],
                        ),
                        dreaming_defaults["repeated_task"]["time_bucket_hours"],
                    ),
                },
                "interest_based": {
                    "enabled": bool(interest_raw.get(
                        "enabled",
                        dreaming_defaults["interest_based"]["enabled"],
                    )),
                },
                "context_cron": _coerce_context_cron(
                    context_cron_raw,
                    dreaming_defaults["context_cron"],
                ),
            },
            "conversation_end": {
                "enabled": bool(conversation_raw.get("enabled", conversation_defaults["enabled"])),
                "max_latency_ms": _positive_int(
                    conversation_raw.get("max_latency_ms", conversation_defaults["max_latency_ms"]),
                    conversation_defaults["max_latency_ms"],
                ),
            },
        },
        "event_hooks": {
            "enabled": bool(event_hooks_raw.get("enabled", event_hooks_defaults["enabled"])),
            "cron_failure": {
                "enabled": bool(cron_failure_raw.get(
                    "enabled",
                    event_hooks_defaults["cron_failure"]["enabled"],
                )),
            },
        },
    }


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
    # BIZ-74 / BIZ-313: active_projects sidecar 설정. dict 이 아니면 빈 dict 로 떨어뜨려
    # 아래 기본값 (~/.simpleclaw-agent/default/active_projects.jsonl) 으로 채운다.
    active_projects = dreaming.get("active_projects", {})
    if not isinstance(active_projects, dict):
        active_projects = {}

    wait_state = daemon.get("wait_state", {})
    if not isinstance(wait_state, dict):
        wait_state = {}
    # BIZ-442: drain 설정. dict 가 아니면 기본값으로 떨어뜨린다.
    drain = daemon.get("drain", {})
    if not isinstance(drain, dict):
        drain = {}
    proactive = daemon.get("proactive", {})

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
            # BIZ-297: 파일별 dreaming 출력 토큰 cap. 양수만 의미 있는 값 — 0/음수/
            # 잘못된 타입은 None 으로 떨어뜨려 프로바이더 기본값으로 fallback.
            "max_tokens": _coerce_dreaming_max_tokens(dreaming.get("max_tokens")),
            # BIZ-313: dreaming sidecar 파일 경로 — 모두 런타임 디렉터리
            # (`~/.simpleclaw-agent/default/`)
            # 기본 경로로 떨어진다. 호출자(run_bot.py 등) 는 *반드시* config 값을
            # 읽어 DreamingPipeline 에 주입해야 한다 (코드 하드코드 금지).
            "insights_file": dreaming.get(
                "insights_file",
                _DAEMON_DEFAULTS["dreaming"]["insights_file"],
            ),
            "suggestions_file": dreaming.get(
                "suggestions_file",
                _DAEMON_DEFAULTS["dreaming"]["suggestions_file"],
            ),
            "blocklist_file": dreaming.get(
                "blocklist_file",
                _DAEMON_DEFAULTS["dreaming"]["blocklist_file"],
            ),
            "runs_file": dreaming.get(
                "runs_file",
                _DAEMON_DEFAULTS["dreaming"]["runs_file"],
            ),
            # BIZ-132 / BIZ-133: safety_backup 디렉터리.
            "safety_backup_dir": dreaming.get(
                "safety_backup_dir",
                _DAEMON_DEFAULTS["dreaming"]["safety_backup_dir"],
            ),
            # BIZ-74 / BIZ-133: active_projects sidecar 설정.
            "active_projects": {
                "enabled": bool(active_projects.get(
                    "enabled",
                    _DAEMON_DEFAULTS["dreaming"]["active_projects"]["enabled"],
                )),
                "window_days": int(active_projects.get(
                    "window_days",
                    _DAEMON_DEFAULTS["dreaming"]["active_projects"]["window_days"],
                )),
                "sidecar_path": active_projects.get(
                    "sidecar_path",
                    _DAEMON_DEFAULTS["dreaming"]["active_projects"]["sidecar_path"],
                ),
            },
        },
        "wait_state": {
            "default_timeout": wait_state.get(
                "default_timeout",
                _DAEMON_DEFAULTS["wait_state"]["default_timeout"],
            ),
        },
        # BIZ-442: drain state 파일 경로 + 요청 자동 만료 시간(초).
        "drain": {
            "state_file": str(
                drain.get("state_file", _DAEMON_DEFAULTS["drain"]["state_file"])
                or _DAEMON_DEFAULTS["drain"]["state_file"]
            ),
            "default_timeout": _positive_int(
                drain.get(
                    "default_timeout",
                    _DAEMON_DEFAULTS["drain"]["default_timeout"],
                ),
                _DAEMON_DEFAULTS["drain"]["default_timeout"],
            ),
        },
        "proactive": _coerce_proactive_policy(proactive),
    }

