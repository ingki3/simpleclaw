#!/usr/bin/env python3
"""Production default composer를 connected no-send 고정 시나리오로 검증한다."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.dev.validate_naver_sports_asset import (
    KBO_SEASON_AUTO_ARGV,
    ProductionAssetValidationError,
    validate_production_asset,
)
from simpleclaw.agent.composition_citations import (
    projected_scalar_is_visible,
)
from simpleclaw.agent.composition_contracts import (
    CompositionInputV1,
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
from simpleclaw.llm.router import create_router
from simpleclaw.memory import ConversationStore
from simpleclaw.recipes.loader import discover_recipes
from simpleclaw.skills.discovery import discover_skills

_FACT = "The activation gate is READY."


class _ProbeInvariantError(RuntimeError):
    """원문을 포함하지 않는 stable activation-gate 실패다."""


class _ForbiddenBoundaryReached(RuntimeError):
    """no-send run이 live sink/supporting dispatch 경계에 진입했음을 표시한다."""


class _OneCallSend:
    """Provider 호출 수를 계측하고 두 번째 호출을 구조적으로 차단한다."""

    def __init__(self, send) -> None:
        self._send = send
        self.calls = 0
        self.provider_citation_count = 0

    async def __call__(self, request):
        self.calls += 1
        if self.calls > 1:
            raise _ProbeInvariantError("composer_provider_call_cap_exceeded")
        response = await self._send(request)
        try:
            payload = json.loads(response.text)
            cited_paths = payload.get("cited_paths", [])
            if isinstance(cited_paths, list) and all(
                isinstance(path, str) for path in cited_paths
            ):
                self.provider_citation_count = len(cited_paths)
        except (AttributeError, TypeError, ValueError):
            self.provider_citation_count = 0
        return response


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


def _definitions():
    recipes = discover_recipes(REPO_ROOT / "runtime_assets" / "recipes")
    skills = discover_skills(
        Path("/__missing_local_skills__"),
        REPO_ROOT / "runtime_assets" / "skills",
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
    persona_prompt: str = (
        "In content, output only the requested projected scalar literals in "
        "contract order. Separate literals only with spaces or commas. Do not "
        "add headings, labels, numbering, relations, explanations, or repeat "
        "any literal."
    )
    source_mode: str = "deterministic_fixture"
    source_evidence: dict[str, object] | None = None


_SCENARIOS = (
    _Scenario(
        name="single_scalar",
        question="Repeat the first projected status exactly.",
        payload={"data": {"items": [{"status": _FACT}]}},
        resolved_claims=("data.items[0].status",),
        expected_citations=("data.items[0].status",),
    ),
    _Scenario(
        name="top_three_items",
        question=(
            "In one sentence without numbering, repeat only the top 3 projected "
            "names and statuses in order."
        ),
        payload={
            "data": {
                "items": [
                    {"name": "Alpha", "status": "One"},
                    {"name": "Beta", "status": "Two"},
                    {"name": "Gamma", "status": "Three"},
                ]
            }
        },
        resolved_claims=(
            "data.items[0].name",
            "data.items[0].status",
            "data.items[1].name",
            "data.items[1].status",
            "data.items[2].name",
            "data.items[2].status",
        ),
        expected_citations=(
            "data.items[0].name",
            "data.items[0].status",
            "data.items[1].name",
            "data.items[1].status",
            "data.items[2].name",
            "data.items[2].status",
        ),
    ),
    _Scenario(
        name="kbo_top_three_fixed",
        question=(
            "현재 KBO 순위 상위 3팀의 팀명과 승수 숫자만, '승' 단위 없이 "
            "쉼표로 나열해줘."
        ),
        payload={
            "data": {
                "mode": "standings",
                "category": "KBO",
                "season": {"code": "2026", "title": "2026 KBO"},
                "date": "2026-08-08",
                "items": [
                    {"rank": 1, "team": "LG", "wins": 60, "losses": 38},
                    {"rank": 2, "team": "한화", "wins": 58, "losses": 40},
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
        persona_prompt=(
            "content에는 요청된 6개 scalar literal만 contract 순서대로 정확히 "
            "한 번씩 쓰세요. 형식은 VALUE SPACE VALUE COMMA SPACE VALUE SPACE "
            "VALUE COMMA SPACE VALUE SPACE VALUE 입니다. VALUE 외에는 공백과 "
            "쉼표만 허용하며 문자 '승', 제목, 설명, 관계어, 단위, 번호, "
            "종결어미를 절대 추가하지 마세요."
        ),
        source_mode="production_shaped_fixed",
    ),
)


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
        name="kbo_top_three_real_read_only",
        question=(
            "현재 KBO 순위 상위 3팀의 팀명과 승수 숫자만, '승' 단위 없이 "
            "쉼표로 나열해줘."
        ),
        payload={"data": result.payload},
        resolved_claims=(
            "data.items[0].team",
            "data.items[0].wins",
            "data.items[1].team",
            "data.items[1].wins",
            "data.items[2].team",
            "data.items[2].wins",
        ),
        expected_citations=(),
        locale="ko-KR",
        persona_prompt=(
            "content에는 요청된 6개 scalar literal만 contract 순서대로 정확히 "
            "한 번씩 쓰세요. 형식은 VALUE SPACE VALUE COMMA SPACE VALUE SPACE "
            "VALUE COMMA SPACE VALUE SPACE VALUE 입니다. VALUE 외에는 공백과 "
            "쉼표만 허용하며 문자 '승', 제목, 설명, 관계어, 단위, 번호, "
            "종결어미를 절대 추가하지 마세요."
        ),
        source_mode="real_read_only",
        source_evidence={
            "asset_text_chars": evidence.asset_text_chars,
            "external_write_count": evidence.external_write_count,
            "helper_cli_executed": evidence.helper_cli_executed,
            "helper_source_sha256": evidence.helper_source_sha256,
            "installation_mode": (
                "production_install" if skill_dir is not None else "exact_checkout_install"
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
        delivery_id="biz-641-spy-preflight",
        request_id="biz-641-spy-preflight",
        artifact_id="biz-641-spy-preflight",
        artifact_hash="biz-641-spy-preflight",
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
    definitions = _definitions()
    counters = _BoundaryCounters()
    capture = _ComposerCapture()
    preflight: dict[str, int]

    async def compose(value: CompositionInputV1) -> DraftResponseV1:
        capture.calls += 1
        capture.value = value
        capture.draft = await composer.compose(value)
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

    def forbidden_conversation(
        _self, _session, _identity, _payload_hash, _content
    ):
        counters.conversation_write += 1
        raise _ForbiddenBoundaryReached("conversation_write")

    def forbidden_sender(*_args):
        raise _ForbiddenBoundaryReached("sender_callback")

    with TemporaryDirectory(
        prefix=f"simpleclaw-biz-641-{scenario.name}-"
    ) as directory:
        temp = Path(directory)
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
                raise _ProbeInvariantError(
                    "conversation_write_spy_preflight_failed"
                )
            preflight = counters.forbidden()
            if set(preflight.values()) != {1}:
                raise _ProbeInvariantError("sink_spy_preflight_count_mismatch")
            counters.reset_forbidden()

            result = await runner.run(
                plan=_plan(definitions, scenario),
                legacy=None,
                request_id=f"biz-641-{scenario.name}-connected-no-send",
                session_key=f"biz-641-{scenario.name}-connected-no-send",
                planner_model_calls=0,
                planner_tokens=0,
            )

        conversation_delta = len(store.get_recent()) - baseline_messages

    if capture.value is None or capture.draft is None:
        raise _ProbeInvariantError("composer_capture_missing")
    guard = guard_final_response(capture.value, capture.draft)
    if not guard.accepted:
        raise _ProbeInvariantError(f"guard_rejected:{guard.code}")
    concrete = flatten_public_facts(capture.value.public_facts)
    if any(
        path not in concrete
        or not projected_scalar_is_visible(
            capture.draft.content,
            concrete[path],
        )
        for path in capture.draft.cited_paths
    ):
        raise _ProbeInvariantError("canonical_citation_not_visible")
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
        "side_effect_delta_zero": all(
            value == 0 for value in measured.values()
        ),
        "forbidden_boundary_zero": all(
            value == 0 for value in forbidden.values()
        ),
        "conversation_delta_zero": conversation_delta == 0,
        "target_dispatch_once": counters.target_dispatch == 1,
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        raise _ProbeInvariantError(
            "connected_no_send_invariant_failed:"
            + ",".join(failed)
        )

    content = capture.draft.content
    return {
        "name": scenario.name,
        "boundary_proof": "connected_primary_no_send_fail_closed_sinks",
        "provider_calls": counted_send.calls,
        "composer_calls": capture.calls,
        "guard_accepted": guard.accepted,
        "guard_code": guard.code,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "content_length": len(content),
        "citations": list(capture.draft.cited_paths),
        "canonical_citation_count": len(capture.draft.cited_paths),
        "provider_citation_count": counted_send.provider_citation_count,
        "pruned_citation_count": max(
            0,
            counted_send.provider_citation_count - len(capture.draft.cited_paths),
        ),
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
    expected_scenarios = len(_SCENARIOS) + int(args.real_kbo)
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
    backend = router.get_default_backend()
    scenarios = list(_SCENARIOS)
    if args.real_kbo:
        try:
            scenarios.append(
                await _real_kbo_scenario(
                    None
                    if args.exact_checkout_install
                    else args.installed_skill_dir.expanduser()
                )
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
    scenario_results: list[dict[str, object]] = []
    provider_calls = 0
    retry_calls = 0
    for scenario in scenarios:
        counted_send = _OneCallSend(router.send)
        composer = FinalResponseComposer(
            send=counted_send,
            persona_prompt=scenario.persona_prompt,
            max_tokens=args.max_tokens,
            backend_name=backend,
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
        except (
            FinalResponseComposerError,
            TimeoutError,
            AttributeError,
            TypeError,
            ValueError,
            _ForbiddenBoundaryReached,
            _ProbeInvariantError,
        ) as exc:
            scenario_results.append(
                {
                    "error_code": (
                        str(exc) if isinstance(exc, _ProbeInvariantError) else None
                    ),
                    "error_type": type(exc).__name__,
                    "name": scenario.name,
                    "provider_calls": counted_send.calls,
                    "retry_calls": max(0, counted_send.calls - 1),
                }
            )
            provider_calls += counted_send.calls
            retry_calls += max(0, counted_send.calls - 1)
            continue
        provider_calls += counted_send.calls
        retry_calls += max(0, counted_send.calls - 1)
        scenario_results.append(scenario_result)

    passed = provider_calls == len(scenarios) and all(
        scenario.get("guard_accepted") is True
        and scenario.get("provider_calls") == 1
        and scenario.get("retry_calls") == 0
        for scenario in scenario_results
    )
    result = {
        "backend": backend,
        "delivery_sinks_configured": True,
        "expected_provider_calls": len(scenarios),
        "passed": passed,
        "provider_calls": provider_calls,
        "retry_calls": retry_calls,
        "scenario_count": len(scenarios),
        "scenarios": scenario_results,
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
    parser.add_argument(
        "--real-kbo",
        action="store_true",
        help="Run one additional real read-only installed KBO helper scenario.",
    )
    parser.add_argument(
        "--installed-skill-dir",
        type=Path,
        default=(
            Path.home()
            / ".agents"
            / "skills"
            / "naver-sports-skill"
        ),
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
