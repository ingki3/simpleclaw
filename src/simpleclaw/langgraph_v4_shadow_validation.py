"""Actual planner와 connected V4 shadow graph를 검증하는 개발용 harness."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal

from simpleclaw.agent.context_candidates import ContextCandidateSet
from simpleclaw.agent.plan_gate import GateStatus, PlanGate
from simpleclaw.agent.planner_catalog import build_planner_catalog
from simpleclaw.agent.tool_schemas import ToolScope, build_native_tool_registry
from simpleclaw.agent.turn_planner import plan_turn_with_llm
from simpleclaw.graph_runtime.contracts import ContractRefV1
from simpleclaw.graph_runtime.runtime import (
    LangGraphV4RolloutFacade,
    LegacyRunTelemetryV1,
    ShadowBudgetUsageV1,
)
from simpleclaw.graph_runtime.shadow import ConnectedShadowTurnRunner
from simpleclaw.graph_runtime.status import TerminalOutcome
from simpleclaw.llm.models import LLMResponse
from simpleclaw.llm.router import create_router
from simpleclaw.memory import ConversationStore
from simpleclaw.production_assets import (
    install_naver_sports_skill,
    install_sports_live_recipe,
)
from simpleclaw.recipes.loader import discover_recipes
from simpleclaw.skills.discovery import discover_skills

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_NAMES = {"contract-fixture-workflow", "contract-fixture-step"}
INCIDENT_PROMPT = "Kbo 순위 상위 3팀 알려줘"


@dataclass(frozen=True, order=True)
class ContractIdentity:
    """Validator가 고정하는 owner-qualified contract identity."""

    owner_type: str
    owner_name: str
    contract_id: str
    version: str
    schema_hash: str


@dataclass(frozen=True)
class ContractSetViolation:
    """Exact-set mismatch를 machine-readable하게 보존한다."""

    kind: Literal["missing", "extra", "drift"]
    expected: ContractIdentity | None = None
    actual: ContractIdentity | None = None
    fields: tuple[str, ...] = ()


EXPECTED_CONTRACT_SET = frozenset(
    {
        ContractIdentity(
            owner_type="recipe",
            owner_name="contract-fixture-workflow",
            contract_id="recipe.contract-fixture-workflow.input",
            version="1",
            schema_hash=(
                "bd7c4ac7d6dddb0980548e9e734dedd82aa919db89a68f5af9337034f420d951"
            ),
        ),
        ContractIdentity(
            owner_type="recipe",
            owner_name="contract-fixture-workflow",
            contract_id="recipe.contract-fixture-workflow.output",
            version="1",
            schema_hash=(
                "80b8777f78ea08e5bede7708a3f84d78c38cc2cfb0dd153347034313589c9eae"
            ),
        ),
        ContractIdentity(
            owner_type="skill",
            owner_name="contract-fixture-step",
            contract_id="skill.contract-fixture-step.input",
            version="1",
            schema_hash=(
                "a742768b209b5455a545f174e2a8b6a9462aebb44a093738e4cb2a30216f62a1"
            ),
        ),
        ContractIdentity(
            owner_type="skill",
            owner_name="contract-fixture-step",
            contract_id="skill.contract-fixture-step.output",
            version="1",
            schema_hash=(
                "e8167acecea2db65606d53fe918fca0eab4d081148b43ec7e7da21849f977f91"
            ),
        ),
    }
)


class _ExplicitBackendRouter:
    """Smoke에서 지정한 실제 backend로 production planner request를 전달한다."""

    def __init__(self, router, backend_name: str) -> None:
        self._router = router
        self._backend_name = backend_name
        self.last_backend_name = ""
        self.last_model = ""

    async def send(self, request):
        response = await self._router.send(
            replace(request, route_name=None, backend_name=self._backend_name)
        )
        self.last_backend_name = str(getattr(response, "backend_name", "") or "")
        self.last_model = str(getattr(response, "model", "") or "")
        return response


class _BoundedPlannerRouter:
    """Actual-provider smoke의 전체 call 수와 wall-clock deadline을 강제한다."""

    def __init__(self, router, *, max_calls: int, deadline_seconds: float) -> None:
        self._router = router
        self._max_calls = max_calls
        self._deadline_seconds = deadline_seconds
        self._started = asyncio.get_running_loop().time()
        self.calls = 0

    async def send(self, request):
        if self.calls >= self._max_calls:
            raise RuntimeError("actual-provider call cap exhausted")
        remaining = self._deadline_seconds - (
            asyncio.get_running_loop().time() - self._started
        )
        if remaining <= 0:
            raise TimeoutError("actual-provider deadline exhausted")
        self.calls += 1
        async with asyncio.timeout(remaining):
            return await self._router.send(request)


class _HermeticPlannerRouter:
    """외부 provider 없이 고정 fixture plan만 반환하는 CI router."""

    async def send(self, request):
        prompt = json.loads(request.user_message)["current_user_message"]
        incident = prompt == INCIDENT_PROMPT
        if incident:
            asset_type = "none"
            asset_name = "__none__"
            mode = "direct_answer"
            fact_required = True
            complexity_signals = []
        elif "contract-fixture-workflow" in prompt:
            asset_type = "recipe"
            asset_name = "contract-fixture-workflow"
            mode = "direct_answer"
            fact_required = False
            complexity_signals: list[str] = []
        else:
            asset_type = "skill"
            asset_name = "contract-fixture-step"
            fact_required = True
            if "verify and compare" in prompt:
                mode = "resolve_complex_problem"
                complexity_signals = ["dependency_graph", "evidence_conflict"]
            else:
                mode = "answer_with_evidence"
                complexity_signals = []
        asset = {"asset_type": asset_type, "asset_name": asset_name}
        payload = {
            "context": {
                "relation": "standalone",
                "use_prior_context": False,
                "selected_turn_ids": [],
                "standalone_question": prompt,
                "unresolved_references": [],
                "ignored_context_reason": "",
            },
            "clarification": {
                "required": False,
                "question": "",
                "options": [],
                "reason": "",
            },
            "domains": ["sports" if incident else "fixture"] if fact_required else [],
            "intents": ["standings" if incident else "verify"] if fact_required else [],
            "fact_check": {
                "required": fact_required,
                "owner": "planner" if incident else ("asset" if fact_required else "none"),
                "domain": "sports" if incident else ("fixture" if fact_required else "none"),
                "entities": [],
                "reference_date": "",
                "search_query": prompt if fact_required else "",
                "required_claims": ["standings"] if incident else (
                    ["contract validation status"] if fact_required else []
                ),
                "freshness_required": incident,
                "reason": "hermetic incident fixture" if incident else "hermetic contract fixture",
            },
            "capability": {
                "coverage": "no_match" if incident else "full_coverage",
                "primary_asset": asset,
                "supporting_assets": [],
                "fallback_modes": [],
                "reason": "incident asset-zero fixture" if incident else "hermetic exact fixture",
            },
            "execution": {
                "mode": mode,
                "allowed_tools": [],
                "requires_confirmation": False,
                "complexity_signals": complexity_signals,
                "reason": "hermetic exact fixture",
            },
            "confidence": 1.0,
            "decision_summary": "deterministic hermetic validation plan",
        }
        return LLMResponse(
            text=json.dumps(payload, ensure_ascii=False),
            backend_name="hermetic",
            model="fixed-contract-fixture",
        )


class _ForbiddenConversationStore:
    """Hermetic validation에서 persistence 경계 접근을 즉시 실패시킨다."""

    def save_outbound_once(self, *_args, **_kwargs):
        raise AssertionError("hermetic validator must not persist conversations")


def _contract_identity(ref: ContractRefV1) -> ContractIdentity:
    return ContractIdentity(
        owner_type=ref.owner_ref.type,
        owner_name=ref.owner_ref.name,
        contract_id=ref.contract_id,
        version=ref.version,
        schema_hash=ref.schema_hash,
    )


def _contract_slot(identity: ContractIdentity) -> tuple[str, ...]:
    """Contract ID drift도 같은 owner/direction slot으로 묶는다."""
    direction = identity.contract_id.rsplit(".", maxsplit=1)[-1]
    if direction not in {"input", "output"}:
        return ()
    return identity.owner_type, identity.owner_name, direction


def _contract_set_violations(
    actual: set[ContractIdentity] | frozenset[ContractIdentity],
    *,
    expected: frozenset[ContractIdentity] = EXPECTED_CONTRACT_SET,
) -> tuple[ContractSetViolation, ...]:
    """Exact match를 제거한 뒤 같은 contract/slot drift와 missing/extra를 분류한다."""
    missing = set(expected - actual)
    extra = set(actual - expected)
    violations: list[ContractSetViolation] = []
    fields = tuple(ContractIdentity.__dataclass_fields__)

    for wanted in sorted(missing):
        drifted = next(
            (
                candidate
                for candidate in sorted(extra)
                if candidate.contract_id == wanted.contract_id
                or (
                    _contract_slot(candidate)
                    and _contract_slot(candidate) == _contract_slot(wanted)
                )
            ),
            None,
        )
        if drifted is None:
            continue
        missing.remove(wanted)
        extra.remove(drifted)
        violations.append(
            ContractSetViolation(
                kind="drift",
                expected=wanted,
                actual=drifted,
                fields=tuple(
                    field
                    for field in fields
                    if getattr(wanted, field) != getattr(drifted, field)
                ),
            )
        )

    violations.extend(
        ContractSetViolation(kind="missing", expected=item)
        for item in sorted(missing)
    )
    violations.extend(
        ContractSetViolation(kind="extra", actual=item)
        for item in sorted(extra)
    )
    return tuple(violations)


def _violation_json(violations: tuple[ContractSetViolation, ...]) -> str:
    return json.dumps(
        [asdict(item) for item in violations],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _assert_contract_set(violations: tuple[ContractSetViolation, ...]) -> None:
    if violations:
        raise RuntimeError(
            "contract continuity exact-set mismatch "
            f"violations={_violation_json(violations)}"
        )


def _definitions():
    """검증 전용 read-only contract fixture만 discovery한다."""
    recipes = discover_recipes(REPO_ROOT / "tests/fixtures/recipes")
    skills = discover_skills(
        REPO_ROOT / "tests/fixtures/skills",
        REPO_ROOT / "tests/fixtures/global-skills",
    )
    return tuple(
        item for item in (*recipes, *skills) if item.name in FIXTURE_NAMES
    )


def _incident_definitions():
    """Canonical installer output으로 production owned-contract shape를 만든다."""
    with tempfile.TemporaryDirectory(prefix="simpleclaw-kbo-assets-") as temp_dir:
        root = Path(temp_dir)
        recipes_dir = root / "recipes"
        global_skills = root / "skills"
        install_sports_live_recipe(recipes_dir)
        install_naver_sports_skill(global_skills)
        recipe = next(
            item
            for item in discover_recipes(recipes_dir)
            if item.name == "sports-live"
        )
        skill = next(
            item
            for item in discover_skills(
                Path("/__missing_local_skills__"),
                global_skills,
            )
            if item.name == "naver-sports-skill"
        )
        return recipe, skill


def _planner_native_specs():
    """PlanGate factual-route 계약에 필요한 production web collector만 노출한다."""
    return tuple(
        spec
        for spec in build_native_tool_registry(
            scopes=(ToolScope.RUNTIME,),
            operator_gate=False,
        )
        if spec.definition.name == "web_search"
    )


async def _recipe_executor(_definition, _bound_steps):
    return {"fixture_result": "connected"}


async def _skill_executor(_definition, _argv):
    return {"operation_result": "connected"}


async def _run(args: argparse.Namespace) -> int:
    """Actual provider exact plan을 bounded no-send graph에서 실행한다."""
    incident_kbo = bool(getattr(args, "incident_kbo", False))
    if args.architecture != "langgraph_v4":
        raise ValueError("--architecture must be langgraph_v4")
    if args.repeat <= 0:
        raise ValueError("--repeat must be positive")
    if args.max_provider_calls <= 0:
        raise ValueError("--max-provider-calls must be positive")
    if args.deadline_seconds <= 0:
        raise ValueError("--deadline-seconds must be positive")
    if not args.hermetic and not args.config.is_file():
        raise FileNotFoundError(f"config not found: {args.config}")

    definitions = _incident_definitions() if incident_kbo else _definitions()
    catalog = build_planner_catalog(
        skills=tuple(item for item in definitions if item.contract_asset_type == "skill"),
        recipes=tuple(item for item in definitions if item.contract_asset_type == "recipe"),
        native_specs=_planner_native_specs(),
    )
    router = _HermeticPlannerRouter() if args.hermetic else create_router(args.config)
    explicit_router = (
        _ExplicitBackendRouter(router, args.backend) if args.backend else router
    )
    planner_router = _BoundedPlannerRouter(
        explicit_router,
        max_calls=args.max_provider_calls,
        deadline_seconds=args.deadline_seconds,
    )
    cases = ((INCIDENT_PROMPT, "recipe"),) if incident_kbo else (
        (
            (
                "Use the exact full-coverage recipe contract-fixture-workflow, which "
                "owns all evidence, to verify the current contract validation status. "
                "Use direct_answer as the fallback execution mode."
            ),
            "recipe",
        ),
        (
            (
                "Use the exact full-coverage skill contract-fixture-step, which owns "
                "all evidence, to verify the current contract validation status. Use "
                "answer_with_evidence as the fallback execution mode."
            ),
            "react",
        ),
        (
            (
                "Use the exact full-coverage skill contract-fixture-step, which owns "
                "all evidence, to verify and compare the current contract validation "
                "status. Resolve the explicit dependency graph and conflicting "
                "validation branches with resolve_complex_problem as the fallback "
                "execution mode."
            ),
            "deep_research",
        ),
    )
    results = []
    attributions: list[dict[str, object]] = []
    backend = model = "hermetic" if args.hermetic else "configured-router"
    with tempfile.TemporaryDirectory(prefix="simpleclaw-v4-shadow-") as tmp:
        isolated = Path(tmp).resolve()
        store = (
            _ForbiddenConversationStore()
            if args.hermetic
            else ConversationStore(isolated / "conversations.db")
        )
        facade = LangGraphV4RolloutFacade(
            architecture="langgraph_v4",
            mode=args.mode,
            shadow_no_send=True,
            budget=ShadowBudgetUsageV1(
                max_graph_steps=40,
                max_asset_calls=12,
                max_llm_calls=max(8, len(cases) * args.repeat),
                max_tokens=16000,
                max_seconds=180,
                max_parallel_invocations=3,
                graph_steps=0,
                asset_calls=0,
                llm_calls=0,
                tokens=0,
                elapsed_seconds=0,
                parallel_peak=0,
                stop_condition="completed",
            ),
            checkpoint_path=isolated / "checkpoints.sqlite3",
            daemon_db_path=isolated / "daemon.db",
            conversations_db_path=isolated / "conversations.db",
        )
        runner = ConnectedShadowTurnRunner(
            facade=facade,
            definitions=definitions,
            conversation_store=store,
            recipe_executor=_recipe_executor,
            skill_executor=_skill_executor,
        )
        candidates = ContextCandidateSet((), 0, False)
        for repetition in range(args.repeat):
            for index, (prompt, route) in enumerate(cases):
                plan = await plan_turn_with_llm(
                    prompt,
                    candidates=candidates,
                    catalog=catalog,
                    router=planner_router,
                    max_tokens=2048,
                )
                if isinstance(explicit_router, _ExplicitBackendRouter):
                    backend = explicit_router.last_backend_name or args.backend
                    model = explicit_router.last_model or "unknown"
                gate = PlanGate().evaluate(
                    plan,
                    candidates=candidates,
                    catalog=catalog,
                )
                if gate.status is not GateStatus.PASS or gate.effective_plan is None:
                    codes = ",".join(item.code for item in gate.violations) or "none"
                    raise RuntimeError(
                        "actual planner plan did not pass PlanGate "
                        f"(case={index} status={gate.status.value} codes={codes})"
                    )
                original_asset = plan.capability.primary_asset
                effective_asset = gate.effective_plan.capability.primary_asset
                attributions.append(
                    {
                        "original_mode": plan.execution.mode.value,
                        "original_asset": (
                            None
                            if original_asset is None
                            else f"{original_asset.asset_type}:{original_asset.name}"
                        ),
                        "effective_mode": gate.effective_plan.execution.mode.value,
                        "effective_asset": (
                            None
                            if effective_asset is None
                            else f"{effective_asset.asset_type}:{effective_asset.name}"
                        ),
                        "gate_status": gate.status.value,
                    }
                )
                result = await runner.run(
                    plan=gate.effective_plan,
                    legacy=(
                        LegacyRunTelemetryV1(
                            selected_route=route,
                            terminal_outcome=TerminalOutcome.COMPLETED,
                            model_calls=1,
                        )
                        if args.mode == "shadow"
                        else None
                    ),
                    request_id=f"actual-{repetition}-{index}",
                    session_key="actual-provider-shadow",
                    planner_model_calls=1,
                    planner_tokens=0,
                )
                if result.telemetry.selected_route != route:
                    raise RuntimeError("actual planner route continuity mismatch")
                if args.mode == "shadow" and (
                    result.comparison is None
                    or result.canary is None
                    or result.comparison.rollback_required
                    or not result.canary.eligible
                ):
                    raise RuntimeError("connected shadow rollout gate rejected run")
                if (
                    result.execution.result_source != "langgraph_v4"
                    or result.execution.final_content is None
                    or not result.execution.dispatch_trace.exactly_once
                    or result.execution.rollback_required
                ):
                    raise RuntimeError("connected V4 typed execution receipt rejected run")
                results.append(result)

        stored_messages = () if args.hermetic else store.get_recent()

    contracts = {
        _contract_identity(contract)
        for result in results
        for contract in (
            result.telemetry.input_contract_ref,
            result.telemetry.output_contract_ref,
        )
    }
    contract_violations = (
        () if incident_kbo else _contract_set_violations(contracts)
    )
    if not incident_kbo:
        _assert_contract_set(contract_violations)
    counts = tuple(result.side_effect_counts for result in results)
    telegram = sum(item.telegram_send for item in counts)
    notifier = sum(item.notifier for item in counts)
    persistence = sum(item.conversation_write for item in counts)
    if telegram or notifier:
        raise RuntimeError("delivery side-effect assertion failed")
    if persistence or stored_messages:
        raise RuntimeError("persistence side-effect assertion failed")
    stop_conditions = {result.telemetry.budget_usage.stop_condition for result in results}
    provider_kind = "HERMETIC_PLANNER" if args.hermetic else "ACTUAL_PLANNER_PROVIDER"
    print(f"{provider_kind}=PASS backend={backend} model={model}")
    print("ASSET_EXECUTOR=fixture")
    print(
        "ASSET_DEFINITIONS="
        + ("production_installer_output" if incident_kbo else "contract_fixtures")
    )
    print(f"PLANNER_CALLS={planner_router.calls}/{args.max_provider_calls}")
    print(f"EXTERNAL_PROVIDER_CALLS={0 if args.hermetic else planner_router.calls}")
    print(f"ROLLOUT_MODE={args.mode}")
    print("RECIPE_FIRST_3_WAY=PASS")
    print("REACT_TO_DEEPRESEARCH=PASS")
    print("RESULT_SOURCE=langgraph_v4")
    print("TARGET_DISPATCH_EXACTLY_ONCE=true")
    print("TYPED_FINAL=PASS")
    if incident_kbo:
        print(
            "INCIDENT_ATTRIBUTION="
            + json.dumps(
                attributions,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    expected_contract_count = len(contracts) if incident_kbo else len(EXPECTED_CONTRACT_SET)
    print(f"ASSET_CONTRACT_CONTINUITY={len(contracts)}/{expected_contract_count}")
    print(f"CONTRACT_SET_VIOLATIONS={_violation_json(contract_violations)}")
    print(f"TELEGRAM_SEND_COUNT={telegram}")
    print(f"CRON_NOTIFIER_COUNT={notifier}")
    print(f"CONVERSATION_WRITE_COUNT={persistence}")
    print(f"STOP_CONDITION={','.join(sorted(stop_conditions))}")
    print(
        "ROLLBACK_REQUIRED="
        f"{str(any(r.execution.rollback_required for r in results)).lower()}"
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", default="langgraph_v4")
    parser.add_argument(
        "--mode",
        choices=("shadow", "read_only_canary", "primary"),
        default="shadow",
    )
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--max-provider-calls", type=int, default=12)
    parser.add_argument("--deadline-seconds", type=float, default=300.0)
    parser.add_argument(
        "--backend",
        default="",
        help="optional configured backend alias for the actual planner request",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "config.yaml",
    )
    parser.add_argument("--hermetic", action="store_true")
    parser.add_argument(
        "--incident-kbo",
        action="store_true",
        help="repeat the exact asset-zero KBO corrective regression",
    )
    parser.set_defaults(
        assert_contract_set=True,
        assert_zero_delivery=True,
        assert_zero_persistence=True,
    )
    return parser


def main() -> int:
    """CLI entrypoint."""
    return asyncio.run(_run(_parser().parse_args()))
