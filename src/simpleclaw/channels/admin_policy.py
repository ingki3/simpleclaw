"""Admin API 정책 엔진 — 적용 등급/검증/영향 모듈 분류.

``docs/admin-requirements.md`` §2.1의 결정 매트릭스를 코드로 옮긴 모듈.

- ``classify_keys``: PATCH로 들어온 ``area + 변경 키 목록``을 살펴 가장 보수적인
  적용 등급(``Hot`` < ``Service-restart`` < ``Process-restart``)과 영향 모듈을
  돌려준다. UI는 이 정보를 바탕으로 dry-run/restart 모달 결정에 사용한다.
- ``validate_patch``: 영역별 단순 타입/범위 검증. 외부 의존(예: API ping)은 본
  모듈의 책임이 아니다 — admin_api 핸들러에서 별도로 호출한다.

설계 결정:

- **딕셔너리 매칭은 "key" 단위**: 하위 키마다 등급이 달라질 수 있으므로 단순 prefix
  매칭이 아닌 dotted-path 매칭을 쓴다. 예: ``daemon.heartbeat_interval``은 Hot,
  ``daemon.db_path``는 Process-restart.
- **와일드카드 한 단계만**: ``llm.providers.*.api_key``처럼 한 단계만 wildcard로
  지원한다. 다단 와일드카드는 정책이 흐려져 운영 디버깅이 어려워진다.
- **검증 실패 시 422**: ``validate_patch``는 ``(ok, errors)`` 튜플을 반환하고,
  핸들러가 422 응답으로 변환한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 등급 상수 — 비교용 정수 가중치도 함께 노출.
HOT = "Hot"
SERVICE_RESTART = "Service-restart"
PROCESS_RESTART = "Process-restart"

_LEVEL_RANK = {HOT: 0, SERVICE_RESTART: 1, PROCESS_RESTART: 2}


def _max_level(a: str, b: str) -> str:
    """두 등급 중 더 보수적인(=재시작에 가까운) 쪽을 돌려준다."""
    return a if _LEVEL_RANK.get(a, 0) >= _LEVEL_RANK.get(b, 0) else b


# ---------------------------------------------------------------------------
# 정책 카탈로그 — admin-requirements.md §1 표를 옮긴 것.
# 키: dotted path. 와일드카드 ``*``는 한 단계 임의 키와 매칭.
# 값: (등급, 영향 모듈 라벨).
# ---------------------------------------------------------------------------
POLICY_CATALOG: dict[str, tuple[str, list[str]]] = {
    # LLM
    "llm.default": (HOT, ["llm.router"]),
    "llm.providers.*": (HOT, ["llm.router"]),
    "llm.providers.*.model": (HOT, ["llm.router"]),
    "llm.providers.*.api_key": (HOT, ["llm.router", "secrets"]),
    "llm.providers.*.type": (HOT, ["llm.router"]),
    # 카테고리별 라우팅 — Admin UI(BIZ-45)에서 도입한 키. 라우터가 다음 호출부터
    # 새 매핑을 사용하므로 데몬 재시작 없이 Hot 등급.
    "llm.routing.*": (HOT, ["llm.router"]),
    # Agent core — 경로 키는 process restart.
    "agent.history_limit": (HOT, ["agent.orchestrator"]),
    "agent.max_tool_iterations": (HOT, ["agent.orchestrator"]),
    "agent.db_path": (PROCESS_RESTART, ["agent.orchestrator", "memory.store"]),
    "agent.workspace_dir": (PROCESS_RESTART, ["agent.orchestrator"]),
    # Memory / RAG
    "memory.rag.enabled": (HOT, ["memory.rag"]),
    "memory.rag.model": (HOT, ["memory.rag"]),
    "memory.rag.top_k": (HOT, ["memory.rag"]),
    "memory.rag.similarity_threshold": (HOT, ["memory.rag"]),
    # Security
    "security.command_guard.enabled": (HOT, ["security.guard"]),
    "security.command_guard.allowlist": (HOT, ["security.guard"]),
    "security.env_passthrough": (HOT, ["security.env_filter"]),
    # Skills
    "skills.execution_timeout": (HOT, ["skills.executor"]),
    "skills.local_dir": (PROCESS_RESTART, ["skills.discovery"]),
    "skills.global_dir": (PROCESS_RESTART, ["skills.discovery"]),
    # MCP — 서버 정의는 재시작 필요.
    "mcp.servers": (SERVICE_RESTART, ["mcp.client"]),
    "mcp.servers.*": (SERVICE_RESTART, ["mcp.client"]),
    # Voice
    "voice.stt.*": (HOT, ["voice.stt"]),
    "voice.tts.*": (HOT, ["voice.tts"]),
    # Telegram
    "telegram.bot_token": (SERVICE_RESTART, ["channels.telegram"]),
    "telegram.whitelist.*": (HOT, ["channels.telegram"]),
    # Webhook
    "webhook.enabled": (SERVICE_RESTART, ["channels.webhook"]),
    "webhook.host": (PROCESS_RESTART, ["channels.webhook"]),
    "webhook.port": (PROCESS_RESTART, ["channels.webhook"]),
    "webhook.auth_token": (HOT, ["channels.webhook", "secrets"]),
    "webhook.max_body_size": (HOT, ["channels.webhook"]),
    "webhook.rate_limit": (HOT, ["channels.webhook"]),
    "webhook.rate_limit_window": (HOT, ["channels.webhook"]),
    "webhook.max_concurrent_connections": (HOT, ["channels.webhook"]),
    "webhook.queue_size": (HOT, ["channels.webhook"]),
    "webhook.alert_cooldown": (HOT, ["channels.webhook"]),
    # Sub-agents
    "sub_agents.max_concurrent": (HOT, ["sub_agents.pool"]),
    "sub_agents.default_timeout": (HOT, ["sub_agents.pool"]),
    "sub_agents.workspace_dir": (PROCESS_RESTART, ["sub_agents.pool"]),
    "sub_agents.cleanup_workspace": (HOT, ["sub_agents.pool"]),
    "sub_agents.default_scope.*": (HOT, ["sub_agents.pool"]),
    # Daemon
    "daemon.heartbeat_interval": (HOT, ["daemon.heartbeat"]),
    "daemon.pid_file": (PROCESS_RESTART, ["daemon"]),
    "daemon.status_file": (PROCESS_RESTART, ["daemon.heartbeat"]),
    "daemon.db_path": (PROCESS_RESTART, ["daemon.store"]),
    "daemon.dreaming.*": (HOT, ["daemon.dreaming"]),
    "daemon.wait_state.*": (HOT, ["daemon.wait_states"]),
    "daemon.cron_retry.*": (HOT, ["daemon.scheduler"]),
    # Persona
    "persona.token_budget": (HOT, ["persona.assembler"]),
    "persona.local_dir": (PROCESS_RESTART, ["persona.resolver"]),
    "persona.global_dir": (PROCESS_RESTART, ["persona.resolver"]),
    "persona.files": (HOT, ["persona.resolver"]),
}


def _path_matches(pattern: str, path: str) -> bool:
    """``foo.*.baz`` 형태 패턴이 dotted ``foo.x.baz`` 경로와 매칭하는지."""
    pattern_parts = pattern.split(".")
    path_parts = path.split(".")
    if len(pattern_parts) > len(path_parts):
        return False
    for pp, sp in zip(pattern_parts, path_parts):
        if pp == "*":
            continue
        if pp != sp:
            return False
    return True


def _flatten(prefix: str, value: object, out: list[str]) -> None:
    """dict 트리를 ``a.b.c`` dotted 경로 리스트로 평탄화한다.

    리스트/스칼라는 그 자리의 경로를 추가하고 더 들어가지 않는다 — 정책은
    리스트 자체에 부여되도록 정의돼 있기 때문(예: ``persona.files``).
    """
    if isinstance(value, dict):
        if not value:
            out.append(prefix)
            return
        for k, v in value.items():
            new_prefix = f"{prefix}.{k}" if prefix else str(k)
            _flatten(new_prefix, v, out)
    else:
        out.append(prefix)


@dataclass
class PolicyResult:
    """``classify_keys`` 결과 — UI/감사 로그 모두에서 그대로 직렬화 가능."""

    level: str  # Hot / Service-restart / Process-restart
    requires_restart: bool
    affected_modules: list[str]
    matched_keys: list[str]


def classify_keys(area: str, patch: dict) -> PolicyResult:
    """PATCH 본문의 dotted 경로를 살펴 가장 보수적인 등급을 결정한다.

    - 매칭 정책이 없는 키는 보수적으로 ``Service-restart``로 간주 — 명세에 없는
      신규 키가 들어왔을 때 데몬 안정성을 위해 운영자에게 추가 확인을 강제한다.
    - ``affected_modules``는 매칭된 모든 정책의 합집합(중복 제거)이다.
    """
    paths: list[str] = []
    _flatten(area, patch, paths)

    level = HOT
    modules: list[str] = []
    matched: list[str] = []

    for path in paths:
        # 가장 구체적으로 매칭되는 정책 — 패턴 길이가 긴 순으로 정렬해 첫 매칭 채택.
        candidates = [
            (pat, lvl, mods)
            for pat, (lvl, mods) in POLICY_CATALOG.items()
            if _path_matches(pat, path)
        ]
        if not candidates:
            level = _max_level(level, SERVICE_RESTART)
            matched.append(path)
            continue
        candidates.sort(key=lambda c: -len(c[0].split(".")))
        pat, lvl, mods = candidates[0]
        level = _max_level(level, lvl)
        for m in mods:
            if m not in modules:
                modules.append(m)
        matched.append(path)

    return PolicyResult(
        level=level,
        requires_restart=level != HOT,
        affected_modules=modules,
        matched_keys=matched,
    )


# ---------------------------------------------------------------------------
# 검증 — admin-requirements.md §1의 "검증" 칸을 단순화해 옮긴 것.
# ---------------------------------------------------------------------------

# Telegram bot 토큰 정규식 — admin-requirements.md §1 9번 항목.
_TELEGRAM_TOKEN_RE = re.compile(r"^\d+:[\w-]{20,}$")


def _check_int_range(
    value: object, lo: int, hi: int, name: str, errors: list[str]
) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        errors.append(f"{name}: 정수가 필요합니다 (got {type(value).__name__})")
        return
    if value < lo or value > hi:
        errors.append(f"{name}: {lo}–{hi} 범위여야 합니다 (got {value})")


def _check_float_range(
    value: object, lo: float, hi: float, name: str, errors: list[str]
) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        errors.append(f"{name}: 실수가 필요합니다 (got {type(value).__name__})")
        return
    if value < lo or value > hi:
        errors.append(f"{name}: {lo}–{hi} 범위여야 합니다 (got {value})")


def validate_patch(area: str, patch: dict) -> list[str]:
    """영역별 PATCH 본문 검증 — 빈 리스트면 통과.

    수용 기준은 의도적으로 보수적이다 — 정상 운영 범위를 벗어난 값을 막는 것이
    목적이며, 사용자가 잘못 입력한 값을 살리려고 자동 보정하지 않는다.
    """
    errors: list[str] = []

    if area == "agent":
        if "history_limit" in patch:
            _check_int_range(patch["history_limit"], 1, 200, "history_limit", errors)
        if "max_tool_iterations" in patch:
            _check_int_range(
                patch["max_tool_iterations"], 1, 20, "max_tool_iterations", errors
            )

    elif area == "memory":
        rag = patch.get("rag", {})
        if isinstance(rag, dict):
            if "top_k" in rag:
                _check_int_range(rag["top_k"], 1, 20, "memory.rag.top_k", errors)
            if "similarity_threshold" in rag:
                _check_float_range(
                    rag["similarity_threshold"],
                    0.0,
                    1.0,
                    "memory.rag.similarity_threshold",
                    errors,
                )

    elif area == "skills":
        if "execution_timeout" in patch:
            _check_int_range(
                patch["execution_timeout"], 5, 600, "skills.execution_timeout", errors
            )

    elif area == "voice":
        tts = patch.get("tts", {})
        if isinstance(tts, dict):
            if "speed" in tts:
                _check_float_range(tts["speed"], 0.25, 4.0, "voice.tts.speed", errors)
            if "max_text_length" in tts:
                _check_int_range(
                    tts["max_text_length"],
                    1,
                    4096,
                    "voice.tts.max_text_length",
                    errors,
                )
            fmt = tts.get("output_format")
            if fmt is not None and fmt not in {"mp3", "opus", "aac", "flac"}:
                errors.append(
                    f"voice.tts.output_format: mp3/opus/aac/flac 중 하나여야 합니다 (got {fmt})"
                )

    elif area == "telegram":
        token = patch.get("bot_token")
        if isinstance(token, str) and token and not token.startswith(
            ("env:", "keyring:", "file:", "plain:")
        ):
            if not _TELEGRAM_TOKEN_RE.match(token):
                errors.append(
                    "telegram.bot_token: 정규식 ^\\d+:[\\w-]{20,}$를 만족해야 합니다"
                )
        whitelist = patch.get("whitelist", {})
        if isinstance(whitelist, dict):
            for key in ("user_ids", "chat_ids"):
                if key in whitelist:
                    ids = whitelist[key]
                    if not isinstance(ids, list) or any(
                        not isinstance(x, int) or isinstance(x, bool) for x in ids
                    ):
                        errors.append(
                            f"telegram.whitelist.{key}: 정수 배열이어야 합니다"
                        )

    elif area == "webhook":
        if "max_body_size" in patch:
            _check_int_range(
                patch["max_body_size"], 1, 16 * 1024 * 1024,
                "webhook.max_body_size", errors,
            )
        if "port" in patch:
            _check_int_range(patch["port"], 1024, 65535, "webhook.port", errors)
        if "rate_limit" in patch:
            _check_int_range(patch["rate_limit"], 0, 100000, "webhook.rate_limit", errors)
        if "rate_limit_window" in patch:
            _check_float_range(
                patch["rate_limit_window"], 0.001, 86400.0,
                "webhook.rate_limit_window", errors,
            )
        if "max_concurrent_connections" in patch:
            _check_int_range(
                patch["max_concurrent_connections"], 1, 1024,
                "webhook.max_concurrent_connections", errors,
            )
        if "queue_size" in patch:
            _check_int_range(
                patch["queue_size"], 0, 8192, "webhook.queue_size", errors
            )

    elif area == "sub_agents":
        if "max_concurrent" in patch:
            _check_int_range(
                patch["max_concurrent"], 1, 10, "sub_agents.max_concurrent", errors
            )
        if "default_timeout" in patch:
            _check_int_range(
                patch["default_timeout"], 1, 86400,
                "sub_agents.default_timeout", errors,
            )

    elif area == "daemon":
        if "heartbeat_interval" in patch:
            _check_int_range(
                patch["heartbeat_interval"], 60, 86400,
                "daemon.heartbeat_interval", errors,
            )
        dreaming = patch.get("dreaming", {})
        if isinstance(dreaming, dict):
            if "overnight_hour" in dreaming:
                _check_int_range(
                    dreaming["overnight_hour"], 0, 23,
                    "daemon.dreaming.overnight_hour", errors,
                )
            if "idle_threshold" in dreaming:
                _check_int_range(
                    dreaming["idle_threshold"], 600, 86400,
                    "daemon.dreaming.idle_threshold", errors,
                )
        cron = patch.get("cron_retry", {})
        if isinstance(cron, dict):
            if "max_attempts" in cron:
                _check_int_range(
                    cron["max_attempts"], 1, 10,
                    "daemon.cron_retry.max_attempts", errors,
                )
            if "backoff_strategy" in cron and cron["backoff_strategy"] not in {
                "linear",
                "exponential",
            }:
                errors.append(
                    "daemon.cron_retry.backoff_strategy: linear/exponential 중 하나여야 합니다"
                )
            if "circuit_break_threshold" in cron:
                _check_int_range(
                    cron["circuit_break_threshold"], 0, 1000,
                    "daemon.cron_retry.circuit_break_threshold", errors,
                )

    elif area == "persona":
        if "token_budget" in patch:
            _check_int_range(
                patch["token_budget"], 512, 32000, "persona.token_budget", errors
            )

    elif area == "llm":
        providers = patch.get("providers", {})
        if isinstance(providers, dict) and "default" in patch:
            default = patch["default"]
            if isinstance(default, str) and providers and default not in providers:
                errors.append(
                    f"llm.default: providers에 정의되지 않은 이름입니다 (got '{default}')"
                )
        # 카테고리 라우팅 — 값은 문자열(provider 이름)이어야 하고, 동시에 PATCH로
        # 들어온 providers가 있다면 그 안에 정의돼 있어야 한다.
        # 부분 PATCH(routing만 보내는 경우)는 서버가 기존 providers를 알지 못하므로
        # 화이트리스트 검증을 생략한다 — admin_api 핸들러가 머지 후 적용한다.
        routing = patch.get("routing", {})
        if isinstance(routing, dict):
            for cat, val in routing.items():
                if not isinstance(cat, str) or not cat:
                    errors.append(f"llm.routing: 카테고리 이름은 비어있지 않은 문자열이어야 합니다")
                    continue
                if val is None:
                    continue
                if not isinstance(val, str):
                    errors.append(
                        f"llm.routing.{cat}: provider 이름(문자열)이어야 합니다 (got {type(val).__name__})"
                    )
                    continue
                if (
                    isinstance(providers, dict) and providers
                    and val not in providers
                ):
                    errors.append(
                        f"llm.routing.{cat}: providers에 정의되지 않은 이름입니다 (got '{val}')"
                    )

    return errors


__all__ = [
    "HOT",
    "SERVICE_RESTART",
    "PROCESS_RESTART",
    "POLICY_CATALOG",
    "PolicyResult",
    "classify_keys",
    "validate_patch",
]
