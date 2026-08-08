#!/usr/bin/env python3
"""Production default composer를 connected no-send 고정 시나리오로 검증한다."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.dev.validate_naver_sports_asset import (
    KBO_SEASON_AUTO_ARGV,
    ProductionAssetValidationError,
    validate_production_asset,
)
from scripts.install_naver_sports_skill import install as install_naver_sports_skill
from scripts.install_sports_live_recipe import (
    install as install_sports_live_recipe,
)
from scripts.install_sports_live_recipe import verify as verify_sports_live_recipe
from simpleclaw.agent.composition_citations import (
    projected_scalar_is_visible,
    projected_scalar_literal_pattern,
)
from simpleclaw.agent.composition_contracts import (
    CompositionInputV1,
    CompositionRenderPlanV1,
    DraftResponseV1,
)
from simpleclaw.agent.composition_projection import flatten_public_facts
from simpleclaw.agent.final_response_composer import (
    FinalResponseComposer,
    FinalResponseComposerError,
)
from simpleclaw.agent.final_response_guard import guard_final_response
from simpleclaw.agent.resolution_types import (
    CapabilityCoverage,
    ExecutionMode,
)
from simpleclaw.agent.turn_plan import (
    AssetRef,
    CapabilityPlan,
    ClarificationPlan,
    ContextRelation,
    ContextSelection,
    EvidenceOwner,
    ExecutionPlan,
    FactCheckPlan,
    UnifiedTurnPlan,
)
from simpleclaw.config_sections.agents import load_agent_config, load_persona_config
from simpleclaw.graph_runtime.adapters.delivery import (
    CronDeliveryAdapter,
    NullDeliveryAdapter,
    TelegramDeliveryAdapter,
)
from simpleclaw.graph_runtime.adapters.persistence import (
    ConversationStorePersistenceAdapter,
)
from simpleclaw.graph_runtime.contracts import DeliveryIntentV1
from simpleclaw.graph_runtime.runtime import (
    DeliveryRuntime,
    LangGraphV4RolloutFacade,
    ShadowBudgetUsageV1,
)
from simpleclaw.graph_runtime.shadow import ConnectedShadowTurnRunner
from simpleclaw.graph_runtime.status import DeliveryStatus
from simpleclaw.llm.models import (
    LLMAuthError,
    LLMError,
    LLMProviderError,
    LLMTimeoutError,
)
from simpleclaw.llm.router import create_router
from simpleclaw.memory import ConversationStore
from simpleclaw.persona.composition_projection import (
    build_composition_persona_projection,
)
from simpleclaw.persona.models import CompositionPersonaProjection
from simpleclaw.persona.resolver import resolve_persona_files
from simpleclaw.recipes.loader import discover_recipes
from simpleclaw.skills.discovery import discover_skills


class _ProbeInvariantError(RuntimeError):
    """원문을 포함하지 않는 stable activation-gate 실패다."""


class _ForbiddenBoundaryReached(RuntimeError):
    """no-send run이 live sink/supporting dispatch 경계에 진입했음을 표시한다."""


@dataclass(frozen=True, slots=True)
class _SanitizedComposerFailure:
    """Provider 원문 없이 Composer 실패 provenance만 보존한다."""

    error_code: str
    error_type: str
    error_stage: str
    cause_type: str | None = None
    root_cause_type: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "cause_type": self.cause_type,
            "error_code": self.error_code,
            "error_stage": self.error_stage,
            "error_type": self.error_type,
            "raw_content_exposed": False,
            "root_cause_type": self.root_cause_type,
        }


class _CapturedComposerFailure(_ProbeInvariantError):
    """Graph fallback 뒤에도 sanitized Composer 실패를 운반한다."""

    def __init__(self, failure: _SanitizedComposerFailure) -> None:
        super().__init__(failure.error_code)
        self.failure = failure


_COMPOSER_ERROR_CODES = {
    "composer returned tool calls": "composer_tool_calls",
    "composer returned an empty response": "composer_empty_response",
    "composer response was invalid": "composer_response_invalid",
}


def _safe_exception_type(exc: BaseException) -> str:
    """예외 payload가 아니라 bounded class name만 진단값으로 반환한다."""
    name = type(exc).__name__
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name) else "Error"


def _exception_chain(exc: BaseException) -> tuple[BaseException, ...]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return tuple(chain)


def _sanitize_composer_failure(exc: FinalResponseComposerError) -> _SanitizedComposerFailure:
    """Composer/Provider 예외를 raw message 없는 stable provenance로 축약한다."""
    chain = _exception_chain(exc)
    provider_error = next((item for item in chain if isinstance(item, LLMError)), None)
    if isinstance(provider_error, LLMAuthError):
        error_code = "provider_auth_error"
        error_stage = "provider"
    elif isinstance(provider_error, LLMTimeoutError) or any(
        isinstance(item, TimeoutError) for item in chain
    ):
        error_code = "provider_timeout"
        error_stage = "provider"
    elif isinstance(provider_error, LLMProviderError):
        error_code = "provider_error"
        error_stage = "provider"
    else:
        error_code = _COMPOSER_ERROR_CODES.get(str(exc), "composer_error")
        error_stage = (
            "composer_parse"
            if error_code == "composer_response_invalid"
            else "composer"
        )
    return _SanitizedComposerFailure(
        error_code=error_code,
        error_type=_safe_exception_type(exc),
        error_stage=error_stage,
        cause_type=(
            _safe_exception_type(chain[1]) if len(chain) > 1 else None
        ),
        root_cause_type=(
            _safe_exception_type(chain[-1]) if len(chain) > 1 else None
        ),
    )


class _OneCallSend:
    """Provider 호출 수를 계측하고 두 번째 호출을 구조적으로 차단한다."""

    def __init__(self, send) -> None:
        self._send = send
        self.calls = 0
        self.provider_plan_shape_valid = False
        self.provider_plan_error_code: str | None = None
        self.provider_plan_validation_signature: tuple[str, ...] = ()

    async def __call__(self, request):
        self.calls += 1
        if self.calls > 1:
            raise _ProbeInvariantError("composer_provider_call_cap_exceeded")
        response = await self._send(request)
        try:
            CompositionRenderPlanV1.model_validate_json(response.text)
            self.provider_plan_shape_valid = True
            self.provider_plan_error_code = None
            self.provider_plan_validation_signature = ()
        except ValidationError as exc:
            self.provider_plan_shape_valid = False
            self.provider_plan_error_code = _render_plan_validation_code(exc)
            self.provider_plan_validation_signature = (
                _render_plan_validation_signature(exc)
            )
        except (AttributeError, TypeError):
            self.provider_plan_shape_valid = False
            self.provider_plan_error_code = "provider_plan_response_type_invalid"
            self.provider_plan_validation_signature = ()
        return response


def _render_plan_validation_signature(exc: ValidationError) -> tuple[str, ...]:
    """입력값·메시지를 제외한 bounded field/error-type signature를 만든다."""
    signature: set[str] = set()
    for item in exc.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    ):
        location = item.get("loc") or ()
        raw_field = str(location[0]) if location else "root"
        field = {
            "schema_version": "schema",
            "schema": "schema",
            "separator": "separator",
            "ending": "ending",
            "root": "root",
        }.get(raw_field, "other")
        raw_error_type = str(item.get("type") or "validation_error")
        error_type = (
            raw_error_type
            if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", raw_error_type)
            else "validation_error"
        )
        signature.add(f"{field}:{error_type}")
    return tuple(sorted(signature))


def _render_plan_validation_code(exc: ValidationError) -> str:
    """Pydantic input/message 없이 render-plan schema 실패 종류만 보존한다."""
    errors = exc.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    )
    error_types = {str(item.get("type")) for item in errors}
    safe_locations = {
        str(item["loc"][0])
        for item in errors
        if item.get("loc")
        and item["loc"][0] in {"schema", "separator", "ending"}
    }
    if "json_invalid" in error_types:
        return "provider_plan_invalid_json"
    if "extra_forbidden" in error_types:
        return "provider_plan_extra_fields"
    for field in ("schema", "separator", "ending"):
        if field in safe_locations and "missing" in error_types:
            return f"provider_plan_{field}_missing"
        if field in safe_locations and "literal_error" in error_types:
            return f"provider_plan_{field}_invalid"
    return "provider_plan_schema_invalid"


@dataclass(slots=True)
class _BoundaryCounters:
    telegram_send: int = 0
    notifier: int = 0
    conversation_write: int = 0
    supporting_dispatch: int = 0
    target_dispatch: int = 0

    def forbidden(self) -> dict[str, int]:
        return {
            "telegram_send": self.telegram_send,
            "notifier": self.notifier,
            "conversation_write": self.conversation_write,
            "supporting_dispatch": self.supporting_dispatch,
        }

    def reset_forbidden(self) -> None:
        self.telegram_send = 0
        self.notifier = 0
        self.conversation_write = 0
        self.supporting_dispatch = 0


@dataclass(slots=True)
class _ComposerCapture:
    calls: int = 0
    value: CompositionInputV1 | None = None
    draft: DraftResponseV1 | None = None
    failure: _SanitizedComposerFailure | None = None


class _ConnectedProbeFacade(LangGraphV4RolloutFacade):
    """실제 adapter 종류를 fail-closed spy로 연결한 no-send facade다."""

    def __init__(
        self,
        *,
        telegram_adapter: TelegramDeliveryAdapter,
        notifier_adapter: CronDeliveryAdapter,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.telegram_adapter = telegram_adapter
        self.notifier_adapter = notifier_adapter

    def shadow_delivery_runtime(self, journal):
        return DeliveryRuntime(
            journal=journal,
            adapters={
                "telegram": self.telegram_adapter,
                "cron": self.notifier_adapter,
                "internal": NullDeliveryAdapter(),
            },
        )


def _definitions(recipes_dir: Path, skills_dir: Path):
    recipes = discover_recipes(recipes_dir)
    skills = discover_skills(
        Path("/__missing_local_skills__"),
        skills_dir,
    )
    selected = tuple(
        item
        for item in (*recipes, *skills)
        if item.name in {"sports-live", "naver-sports-skill"}
    )
    if len(selected) != 2:
        raise _ProbeInvariantError("production_asset_definition_missing")
    return selected


@dataclass(frozen=True, slots=True)
class _Scenario:
    name: str
    question: str
    payload: dict[str, object]
    resolved_claims: tuple[str, ...]
    expected_citations: tuple[str, ...]
    locale: str = "en-US"
    source_mode: str = "deterministic_fixture"
    source_evidence: dict[str, object] | None = None


_NATURAL_KBO_SCENARIO = _Scenario(
    name="production_persona_natural_kbo",
    question="현재 KBO 순위 상위 3팀을 승수와 함께 알려줘",
    payload={
        "data": {
            "mode": "standings",
            "category": "KBO",
            "season": {"code": "2026", "title": "2026 KBO"},
            "date": "2026-08-08",
            "items": [
                {"rank": 1, "team": "LG", "wins": 60, "losses": 38},
                {"rank": 2, "team": "한화", "wins": 55, "losses": 40},
                {"rank": 3, "team": "롯데", "wins": 55, "losses": 43},
            ],
        }
    },
    resolved_claims=(
        "data.items[0].team",
        "data.items[0].wins",
        "data.items[1].team",
        "data.items[1].wins",
        "data.items[2].team",
        "data.items[2].wins",
    ),
    expected_citations=(
        "data.items[0].team",
        "data.items[0].wins",
        "data.items[1].team",
        "data.items[1].wins",
        "data.items[2].team",
        "data.items[2].wins",
    ),
    locale="ko-KR",
    source_mode="production_shaped_fixed",
)

_SCHEDULE_PRESENT_SCENARIO = _Scenario(
    name="production_schedule_present",
    question="오늘 프로야구 하냐?",
    payload={
        "data": {
            "mode": "schedule",
            "status": "ok",
            "items": [
                {
                    "event_state": "scheduled",
                    "status_code": "BEFORE",
                    "status": "경기 예정",
                    "started_at": "2026-08-08T18:30:00+09:00",
                    "participants": {
                        "away": {"name": "두산"},
                        "home": {"name": "한화"},
                    },
                    "source_url": "https://api-gw.sports.naver.com/schedule/games",
                }
            ],
        }
    },
    resolved_claims=(
        "data.items[0].status",
        "data.items[0].participants.away.name",
        "data.items[0].participants.home.name",
    ),
    expected_citations=(
        "data.items[0].status",
        "data.items[0].participants.away.name",
        "data.items[0].participants.home.name",
    ),
    locale="ko-KR",
    source_mode="production_shaped_fixed",
)

_SCHEDULE_EMPTY_SCENARIO = _Scenario(
    name="production_schedule_empty",
    question="오늘 프로야구 하냐?",
    payload={
        "data": {
            "mode": "schedule",
            "status": "empty",
            "empty_reason": "no_scheduled_events",
            "items": [],
        }
    },
    resolved_claims=("data.status", "data.empty_reason"),
    expected_citations=("data.status", "data.empty_reason"),
    locale="ko-KR",
    source_mode="production_shaped_fixed",
)

_LIVE_EMPTY_SCENARIO = _Scenario(
    name="production_live_empty",
    question="지금 KBO 경기 중이야?",
    payload={
        "data": {
            "mode": "live",
            "status": "empty",
            "empty_reason": "no_live_events",
            "items": [],
        }
    },
    resolved_claims=("data.status", "data.empty_reason"),
    expected_citations=("data.status", "data.empty_reason"),
    locale="ko-KR",
    source_mode="production_shaped_fixed",
)


def _independent_scenarios(base: _Scenario) -> tuple[_Scenario, ...]:
    return tuple(
        _Scenario(
            name=f"{base.name}_{run}",
            question=base.question,
            payload=base.payload,
            resolved_claims=base.resolved_claims,
            expected_citations=base.expected_citations,
            locale=base.locale,
            source_mode=base.source_mode,
            source_evidence=base.source_evidence,
        )
        for run in range(1, 4)
    )


_SCENARIOS = _independent_scenarios(_NATURAL_KBO_SCENARIO)


def _bounded_backend_names(
    default_backend: str,
    overrides: list[str] | tuple[str, ...],
    available: list[str] | tuple[str, ...],
    *,
    only_backend: str | None = None,
) -> tuple[str, ...]:
    """Runtime default를 바꾸지 않고 최대 두 alternate만 eval에 추가한다."""
    normalized = tuple(
        dict.fromkeys(item.strip() for item in overrides if item.strip())
    )
    if only_backend is not None and normalized:
        raise _ProbeInvariantError("backend_selection_conflict")
    if len(normalized) > 2:
        raise _ProbeInvariantError("backend_override_limit_exceeded")
    if only_backend is not None:
        selected_name = only_backend.strip()
        if not selected_name:
            raise _ProbeInvariantError("backend_selection_empty")
        selected = (selected_name,)
    else:
        selected = tuple(dict.fromkeys((default_backend, *normalized)))
    unknown = tuple(item for item in selected if item not in available)
    if unknown:
        raise _ProbeInvariantError("backend_override_unknown")
    return selected


def _first_failure(
    scenario_results: list[dict[str, object]],
) -> dict[str, object] | None:
    """후속 성공이 앞선 실패 증거를 지우지 않도록 첫 실패를 복사한다."""
    return next(
        (
            dict(item)
            for item in scenario_results
            if item.get("guard_accepted") is not True
        ),
        None,
    )


def _production_persona_projection(
    config_path: Path,
) -> CompositionPersonaProjection:
    """Orchestrator와 같은 live runtime persona projection을 만든다."""
    config = load_persona_config(config_path)
    persona_files = resolve_persona_files(
        local_dir=config["local_dir"],
        global_dir=config["global_dir"],
    )
    composition_config = config["composition"]
    projection = build_composition_persona_projection(
        persona_files,
        token_budget=int(composition_config["token_budget"]),
        section_policy=composition_config["sections"],
    )
    if not projection.instruction_text:
        raise _ProbeInvariantError("production_persona_missing")
    return projection


def _limit_three_argv() -> tuple[str, ...]:
    argv = list(KBO_SEASON_AUTO_ARGV)
    argv[argv.index("--limit") + 1] = "3"
    return tuple(argv)


async def _real_kbo_scenario(skill_dir: Path | None) -> _Scenario:
    result = await asyncio.to_thread(
        validate_production_asset,
        skill_dir,
        source_mode="real_read_only",
        argv=_limit_three_argv(),
        expected_result_limit=3,
    )
    evidence = result.evidence
    return _Scenario(
        name="production_persona_natural_kbo",
        question="현재 KBO 순위 상위 3팀을 승수와 함께 알려줘",
        payload={"data": result.payload},
        resolved_claims=(
            "data.items[0].team",
            "data.items[0].wins",
            "data.items[1].team",
            "data.items[1].wins",
            "data.items[2].team",
            "data.items[2].wins",
        ),
        expected_citations=(
            "data.items[0].team",
            "data.items[0].wins",
            "data.items[1].team",
            "data.items[1].wins",
            "data.items[2].team",
            "data.items[2].wins",
        ),
        locale="ko-KR",
        source_mode="real_read_only",
        source_evidence={
            "asset_text_chars": evidence.asset_text_chars,
            "external_write_count": evidence.external_write_count,
            "helper_cli_executed": evidence.helper_cli_executed,
            "helper_source_sha256": evidence.helper_source_sha256,
            "installation_mode": (
                "production_install"
                if skill_dir is not None
                else "exact_checkout_install"
            ),
            "item_count": evidence.item_count,
            "requested_limit": evidence.requested_limit,
        },
    )


def _plan(definitions, scenario: _Scenario) -> UnifiedTurnPlan:
    recipe = next(item for item in definitions if item.name == "sports-live")
    asset = AssetRef(asset_type="recipe", name=recipe.name)
    return UnifiedTurnPlan(
        original_text=scenario.question,
        context=ContextSelection(
            relation=ContextRelation.STANDALONE,
            use_prior_context=False,
            selected_turn_ids=(),
            standalone_question=scenario.question,
        ),
        clarification=ClarificationPlan(required=False),
        domains=("fixture",),
        intents=("verify",),
        fact_check=FactCheckPlan(
            required=False,
            owner=EvidenceOwner.NONE,
            domain="fixture",
            entities=(),
            search_query="",
        ),
        execution=ExecutionPlan(
            mode=ExecutionMode.DIRECT_ANSWER,
            primary_asset=asset,
            allowed_assets=(asset,),
        ),
        capability=CapabilityPlan(
            coverage=CapabilityCoverage.FULL,
            primary_asset=asset,
            supporting_assets=(),
        ),
        confidence=1.0,
        decision_summary="synthetic connected citation activation gate",
        approved_asset_fingerprint=recipe.definition_fingerprint,
    )


def _budget(timeout: float) -> ShadowBudgetUsageV1:
    return ShadowBudgetUsageV1(
        max_graph_steps=40,
        max_asset_calls=2,
        max_llm_calls=1,
        max_tokens=2400,
        max_seconds=timeout,
        max_parallel_invocations=1,
        graph_steps=0,
        asset_calls=0,
        llm_calls=0,
        tokens=0,
        elapsed_seconds=0,
        parallel_peak=0,
        stop_condition="completed",
    )


def _dummy_intent() -> DeliveryIntentV1:
    return DeliveryIntentV1(
        delivery_id="biz-643-spy-preflight",
        request_id="biz-643-spy-preflight",
        artifact_id="biz-643-spy-preflight",
        artifact_hash="biz-643-spy-preflight",
        channel="telegram",
        destination_ref="forbidden:no-send",
        status=DeliveryStatus.READY,
        max_attempts=1,
    )


async def _connected_probe(
    *,
    composer: FinalResponseComposer,
    counted_send: _OneCallSend,
    scenario: _Scenario,
    timeout: float,
) -> dict[str, object]:
    counters = _BoundaryCounters()
    capture = _ComposerCapture()
    preflight: dict[str, int]

    async def compose(value: CompositionInputV1) -> DraftResponseV1:
        capture.calls += 1
        capture.value = value
        try:
            capture.draft = await composer.compose(value)
        except FinalResponseComposerError as exc:
            capture.failure = _sanitize_composer_failure(exc)
            raise
        return capture.draft

    async def target_executor(_definition, _bound_steps):
        counters.target_dispatch += 1
        if counters.target_dispatch > 1:
            raise _ProbeInvariantError("target_dispatch_cap_exceeded")
        return {
            "status": "completed",
            "side_effect": False,
            **scenario.payload,
            "resolved_claims": list(scenario.resolved_claims),
            "unresolved_claims": [],
        }

    async def forbidden_supporting_dispatch(_definition, _argv):
        counters.supporting_dispatch += 1
        raise _ForbiddenBoundaryReached("supporting_dispatch")

    async def forbidden_telegram(_self, _intent, _content):
        counters.telegram_send += 1
        raise _ForbiddenBoundaryReached("telegram_send")

    async def forbidden_notifier(_self, _intent, _content):
        counters.notifier += 1
        raise _ForbiddenBoundaryReached("notifier")

    def forbidden_conversation(_self, _session, _identity, _payload_hash, _content):
        counters.conversation_write += 1
        raise _ForbiddenBoundaryReached("conversation_write")

    def forbidden_sender(*_args):
        raise _ForbiddenBoundaryReached("sender_callback")

    with TemporaryDirectory(prefix=f"simpleclaw-biz-643-{scenario.name}-") as directory:
        temp = Path(directory)
        recipes_dir = temp / "runtime-recipes"
        skills_dir = temp / "runtime-skills"
        install_sports_live_recipe(recipes_dir)
        install_naver_sports_skill(skills_dir)
        verify_sports_live_recipe(recipes_dir)
        definitions = _definitions(recipes_dir, skills_dir)
        store = ConversationStore(temp / "conversation.db")
        baseline_messages = len(store.get_recent())
        facade = _ConnectedProbeFacade(
            architecture="langgraph_v4",
            mode="primary",
            shadow_no_send=True,
            budget=_budget(timeout),
            checkpoint_path=temp / "checkpoint.sqlite3",
            conversations_db_path=temp / "conversation.db",
            telegram_adapter=TelegramDeliveryAdapter(forbidden_sender),
            notifier_adapter=CronDeliveryAdapter(forbidden_sender),
        )
        runner = ConnectedShadowTurnRunner(
            facade=facade,
            definitions=definitions,
            conversation_store=store,
            recipe_executor=target_executor,
            skill_executor=forbidden_supporting_dispatch,
            composition_mode="central_persona_v1",
            response_composer=compose,
            composer_fingerprint=composer.fingerprint,
            locale=scenario.locale,
        )

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(TelegramDeliveryAdapter, "send", forbidden_telegram)
            )
            stack.enter_context(
                patch.object(CronDeliveryAdapter, "send", forbidden_notifier)
            )
            stack.enter_context(
                patch.object(
                    ConversationStorePersistenceAdapter,
                    "__call__",
                    forbidden_conversation,
                )
            )
            dummy = _dummy_intent()
            async_checks: tuple[tuple[str, Callable], ...] = (
                (
                    "telegram_send",
                    lambda: facade.telegram_adapter.send(dummy, "preflight"),
                ),
                (
                    "notifier",
                    lambda: facade.notifier_adapter.send(dummy, "preflight"),
                ),
                (
                    "supporting_dispatch",
                    lambda: forbidden_supporting_dispatch(None, ()),
                ),
            )
            for name, invoke in async_checks:
                try:
                    await invoke()
                except _ForbiddenBoundaryReached:
                    pass
                else:
                    raise _ProbeInvariantError(f"{name}_spy_preflight_failed")
            try:
                ConversationStorePersistenceAdapter(store)(
                    "preflight",
                    "preflight",
                    "preflight",
                    "preflight",
                )
            except _ForbiddenBoundaryReached:
                pass
            else:
                raise _ProbeInvariantError("conversation_write_spy_preflight_failed")
            preflight = counters.forbidden()
            if set(preflight.values()) != {1}:
                raise _ProbeInvariantError("sink_spy_preflight_count_mismatch")
            counters.reset_forbidden()

            result = await runner.run(
                plan=_plan(definitions, scenario),
                legacy=None,
                request_id=f"biz-643-{scenario.name}-connected-no-send",
                session_key=f"biz-643-{scenario.name}-connected-no-send",
                planner_model_calls=0,
                planner_tokens=0,
            )

        conversation_delta = len(store.get_recent()) - baseline_messages

    if capture.value is None or capture.draft is None:
        if capture.failure is not None:
            raise _CapturedComposerFailure(capture.failure)
        raise _ProbeInvariantError("composer_capture_missing")
    guard = guard_final_response(capture.value, capture.draft)
    if not guard.accepted:
        content = capture.draft.content
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        citations = ",".join(capture.draft.cited_paths)
        concrete = flatten_public_facts(capture.value.public_facts)
        visible_uncited = ",".join(
            path
            for path, projected in concrete.items()
            if path not in capture.draft.cited_paths
            and projected_scalar_is_visible(content, projected)
        )
        rendered_positions: list[tuple[int, str]] = []
        for path in capture.draft.cited_paths:
            pattern = projected_scalar_literal_pattern(concrete[path])
            match = pattern.search(content) if pattern is not None else None
            if match is not None:
                rendered_positions.append((match.start(), path))
        render_order = ",".join(path for _, path in sorted(rendered_positions))
        lexical_probe = content
        for projected in concrete.values():
            if isinstance(projected, str) and projected.strip():
                lexical_probe = re.sub(
                    re.escape(projected.strip()),
                    "",
                    lexical_probe,
                    flags=re.IGNORECASE,
                )
        token_hashes = ",".join(
            hashlib.sha256(token.encode("utf-8")).hexdigest()
            for token in re.findall(r"[^\W\d_]+", lexical_probe)
        )
        raise _ProbeInvariantError(
            f"guard_rejected:{guard.code}:content_sha256={digest}:"
            f"content_length={len(content)}:citations={citations}:"
            f"visible_uncited_paths={visible_uncited}:render_order={render_order}:"
            f"lexical_token_sha256={token_hashes}"
        )
    measured = result.side_effect_counts.as_dict()
    forbidden = counters.forbidden()
    checks = {
        "provider_once": counted_send.calls == 1,
        "composer_once": capture.calls == 1,
        "guard_accepted": guard.accepted,
        "draft_promoted": result.execution.final_content == capture.draft.content,
        "delivery_shadowed": (
            result.telemetry.delivery_status is DeliveryStatus.SHADOWED
        ),
        "rollback_clear": result.execution.rollback_required is False,
        "side_effect_delta_zero": all(value == 0 for value in measured.values()),
        "forbidden_boundary_zero": all(value == 0 for value in forbidden.values()),
        "conversation_delta_zero": conversation_delta == 0,
        "target_dispatch_once": counters.target_dispatch == 1,
        "provider_plan_shape_valid": counted_send.provider_plan_shape_valid,
        "source_citations_exact": (
            tuple(capture.draft.cited_paths) == scenario.expected_citations
        ),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        raise _ProbeInvariantError(
            "connected_no_send_invariant_failed:" + ",".join(failed)
        )

    content = capture.draft.content
    return {
        "name": scenario.name,
        "boundary_proof": "connected_primary_no_send_fail_closed_sinks",
        "provider_calls": counted_send.calls,
        "composer_calls": capture.calls,
        "temperature": composer.temperature,
        "guard_accepted": guard.accepted,
        "guard_code": guard.code,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "content_length": len(content),
        "citations": list(capture.draft.cited_paths),
        "canonical_citation_count": len(capture.draft.cited_paths),
        "provider_plan_shape_valid": counted_send.provider_plan_shape_valid,
        "delivery_status": result.telemetry.delivery_status.value,
        "sink_spy_preflight_calls": preflight,
        "measured_side_effect_deltas": measured,
        "measured_forbidden_boundary_calls": forbidden,
        "conversation_store_message_delta": conversation_delta,
        "connected_target_dispatch_calls": counters.target_dispatch,
        "retry_calls": max(0, counted_send.calls - 1),
        "source_mode": scenario.source_mode,
        "source_evidence": scenario.source_evidence,
        "configured_sink_boundaries": [
            "telegram_adapter",
            "cron_notifier_adapter",
            "conversation_store_writer",
            "supporting_asset_dispatch",
        ],
    }


async def _run(args: argparse.Namespace) -> int:
    config_path = args.config.expanduser()
    expected_scenarios = 3
    if not config_path.is_file():
        print(
            json.dumps(
                {
                    "delivery_sinks_configured": False,
                    "error_type": "ConfigNotFound",
                    "expected_provider_calls": expected_scenarios,
                    "provider_calls": 0,
                    "retry_calls": 0,
                    "scenario_count": expected_scenarios,
                },
                sort_keys=True,
            )
        )
        return 1

    logging.disable(logging.CRITICAL)
    router = create_router(config_path)
    default_backend = router.get_default_backend()
    try:
        backends = _bounded_backend_names(
            default_backend,
            args.backend,
            router.list_backends(),
            only_backend=args.only_backend,
        )
    except _ProbeInvariantError as exc:
        print(
            json.dumps(
                {
                    "error_code": str(exc),
                    "error_type": type(exc).__name__,
                    "passed": False,
                },
                sort_keys=True,
            )
        )
        return 1
    agent_config = load_agent_config(config_path)
    planner_config = agent_config.get("unified_turn_planner", {})
    langgraph_config = planner_config.get("langgraph_v4", {})
    composition_config = langgraph_config.get("composition", {})
    temperature = float(composition_config.get("temperature", 0.0))
    base_scenario = _NATURAL_KBO_SCENARIO
    if args.real_kbo:
        try:
            base_scenario = await _real_kbo_scenario(
                None
                if args.exact_checkout_install
                else args.installed_skill_dir.expanduser()
            )
        except ProductionAssetValidationError as exc:
            print(
                json.dumps(
                    {
                        "error_code": exc.code,
                        "error_type": type(exc).__name__,
                        "passed": False,
                        "raw_content_exposed": False,
                    },
                    sort_keys=True,
                )
            )
            return 1
        except Exception:  # noqa: BLE001
            print(
                json.dumps(
                    {
                        "error_code": "unexpected_real_kbo_failure",
                        "error_type": "RealKboSourceError",
                        "passed": False,
                        "raw_content_exposed": False,
                    },
                    sort_keys=True,
                )
            )
            return 1
    try:
        persona_projection = _production_persona_projection(config_path)
    except (OSError, TypeError, ValueError, _ProbeInvariantError) as exc:
        print(
            json.dumps(
                {
                    "error_code": (
                        str(exc) if isinstance(exc, _ProbeInvariantError) else None
                    ),
                    "error_type": type(exc).__name__,
                    "passed": False,
                    "raw_content_exposed": False,
                },
                sort_keys=True,
            )
        )
        return 1
    scenarios = (
        list(_independent_scenarios(base_scenario))
        if args.real_kbo
        else list(_SCENARIOS)
    )
    backend_results: list[dict[str, object]] = []
    provider_calls = 0
    retry_calls = 0
    for backend in backends:
        scenario_results: list[dict[str, object]] = []
        backend_provider_calls = 0
        backend_retry_calls = 0
        for scenario in scenarios:
            counted_send = _OneCallSend(router.send)
            composer = FinalResponseComposer(
                send=counted_send,
                persona_projection=persona_projection,
                max_tokens=args.max_tokens,
                backend_name=backend,
                temperature=temperature,
            )
            try:
                scenario_result = await asyncio.wait_for(
                    _connected_probe(
                        composer=composer,
                        counted_send=counted_send,
                        scenario=scenario,
                        timeout=args.timeout,
                    ),
                    timeout=args.timeout,
                )
            except _CapturedComposerFailure as exc:
                scenario_result = {
                    "backend": backend,
                    **exc.failure.as_dict(),
                    "name": scenario.name,
                    "provider_plan_error_code": (
                        counted_send.provider_plan_error_code
                    ),
                    "provider_plan_validation_signature": list(
                        counted_send.provider_plan_validation_signature
                    ),
                    "provider_calls": counted_send.calls,
                    "retry_calls": max(0, counted_send.calls - 1),
                }
            except (
                FinalResponseComposerError,
                TimeoutError,
                AttributeError,
                TypeError,
                ValueError,
                _ForbiddenBoundaryReached,
                _ProbeInvariantError,
            ) as exc:
                sanitized = (
                    _sanitize_composer_failure(exc).as_dict()
                    if isinstance(exc, FinalResponseComposerError)
                    else None
                )
                scenario_result = {
                    "backend": backend,
                    "error_code": (
                        str(exc) if isinstance(exc, _ProbeInvariantError) else None
                    ),
                    "error_type": type(exc).__name__,
                    "name": scenario.name,
                    "provider_calls": counted_send.calls,
                    "raw_content_exposed": False,
                    "retry_calls": max(0, counted_send.calls - 1),
                }
                if sanitized is not None:
                    scenario_result.update(sanitized)
                    scenario_result["provider_plan_error_code"] = (
                        counted_send.provider_plan_error_code
                    )
                    scenario_result["provider_plan_validation_signature"] = list(
                        counted_send.provider_plan_validation_signature
                    )
            scenario_result["backend"] = backend
            scenario_results.append(scenario_result)
            backend_provider_calls += counted_send.calls
            backend_retry_calls += max(0, counted_send.calls - 1)
        backend_passed = backend_provider_calls == len(scenarios) and all(
            scenario.get("guard_accepted") is True
            and scenario.get("provider_calls") == 1
            and scenario.get("retry_calls") == 0
            for scenario in scenario_results
        )
        backend_results.append(
            {
                "backend": backend,
                "first_failure": _first_failure(scenario_results),
                "pass_count": sum(
                    scenario.get("guard_accepted") is True
                    for scenario in scenario_results
                ),
                "passed": backend_passed,
                "provider_calls": backend_provider_calls,
                "retry_calls": backend_retry_calls,
                "scenario_count": len(scenarios),
                "scenarios": scenario_results,
            }
        )
        provider_calls += backend_provider_calls
        retry_calls += backend_retry_calls

    passed = all(item["passed"] is True for item in backend_results)
    first_failure = next(
        (
            dict(item["first_failure"])
            for item in backend_results
            if item["first_failure"] is not None
        ),
        None,
    )
    result = {
        "backend_results": backend_results,
        "default_backend": default_backend,
        "delivery_sinks_configured": True,
        "evaluated_backends": list(backends),
        "expected_provider_calls": len(scenarios) * len(backends),
        "first_failure": first_failure,
        "passed": passed,
        "provider_calls": provider_calls,
        "retry_calls": retry_calls,
        "scenario_count": len(scenarios),
        "temperature": temperature,
        "persona_source_types": [
            source_type.value for source_type in persona_projection.source_types
        ],
        "persona_policy_version": persona_projection.policy_version,
        "persona_fingerprint": persona_projection.fingerprint,
        "persona_content_hash": hashlib.sha256(
            persona_projection.instruction_text.encode("utf-8")
        ).hexdigest(),
        "persona_length": len(persona_projection.instruction_text),
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if passed else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path.home() / ".simpleclaw" / "config.yaml",
    )
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--timeout", type=float, default=120.0)
    backend_group = parser.add_mutually_exclusive_group()
    backend_group.add_argument(
        "--backend",
        action="append",
        default=[],
        help=(
            "Add up to two configured alternate backends for this eval only; "
            "the runtime default remains unchanged."
        ),
    )
    backend_group.add_argument(
        "--only-backend",
        help=(
            "Evaluate exactly one configured backend without implicitly re-running "
            "the runtime default."
        ),
    )
    parser.add_argument(
        "--real-kbo",
        action="store_true",
        help="Use one real read-only KBO payload for all three independent runs.",
    )
    parser.add_argument(
        "--installed-skill-dir",
        type=Path,
        default=(Path.home() / ".agents" / "skills" / "naver-sports-skill"),
    )
    parser.add_argument(
        "--exact-checkout-install",
        action="store_true",
        help=(
            "Install the exact checkout asset into a temporary directory for the "
            "real read-only helper scenario."
        ),
    )
    return parser


def main() -> int:
    return asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
