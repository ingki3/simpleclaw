"""BIZ-578 fixed-gold LangGraph V4 사용자 시나리오 평가기.

Planner 입력에는 fixture 원문이 필요하지만 결과 경계는 case ID와 정규화된
판정만 허용한다. 이 모듈은 Telegram, cron notifier, ConversationStore를 직접
참조하지 않으며, contract-complete read-only 자산만 명시적 callback으로 넘긴다.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from simpleclaw.agent.context_candidates import (
    ContextCandidate,
    ContextCandidateSet,
    ContextTrust,
)
from simpleclaw.agent.plan_gate import GateStatus, PlanGate, PlanGateResult
from simpleclaw.agent.planner_catalog import PlannerAsset, PlannerCatalog
from simpleclaw.agent.resolution_types import ExecutionMode
from simpleclaw.agent.turn_plan import AssetRef, UnifiedTurnPlan
from simpleclaw.agent.turn_planner import PlannerUnavailable, plan_turn_with_llm
from simpleclaw.graph_runtime.runtime import (
    LangGraphV4RolloutFacade,
    LegacyRunTelemetryV1,
    ShadowBudgetUsageV1,
)
from simpleclaw.graph_runtime.shadow import ConnectedShadowTurnRunner
from simpleclaw.graph_runtime.status import TerminalOutcome
from simpleclaw.memory import ConversationStore

SCHEMA_VERSION = "langgraph-v4-user-scenarios.v2"
REPORT_SCHEMA_VERSION = "langgraph-v4-scenario-eval.v2"
V4_ROUTES = frozenset(
    {
        "simple_conversation",
        "command_bypass",
        "recipe_command_bypass",
        "recipe",
        "react",
        "deep_research",
        "interrupt",
    }
)
GRAPH_SCOPES = frozenset(
    {
        "ingress_bypass",
        "synthetic_contract_probe",
        "planner_only",
        "interrupt_only",
    }
)
EVALUATION_SCOPES = frozenset(
    {
        "runtime_scored",
        "ingress_bypass",
        "operator_scope_gap",
        "attachment_scope_gap",
    }
)
_NATIVE_INGRESS_COMMANDS = frozenset({"cron", "skills"})
SIDE_EFFECT_POLICIES = frozenset(
    {"none", "read_only_command", "confirmation_before_dispatch", "clarification_only"}
)
_RELATIONS = frozenset(
    {"standalone", "same_thread", "related_reference", "topic_shift", "unclear"}
)
_MODES = frozenset(item.value for item in ExecutionMode)
_ID_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
_SAFE_DIMENSION_RE = re.compile(r"[^A-Za-z0-9_.:/+-]+")
_SECRET_RE = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:api[_-]?key|token|secret|password)\s*[:=]|"
    r"\bsk-[A-Za-z0-9_-]{8,})"
)
_FORBIDDEN_REPORT_KEYS = frozenset(
    {
        "api_key",
        "content",
        "credential",
        "current",
        "history",
        "original_text",
        "password",
        "query",
        "search_query",
        "secret",
        "standalone_question",
        "token",
    }
)


class ScenarioFixtureError(ValueError):
    """Fixture가 strict schema를 위반했다."""


class ProviderBudgetExceeded(RuntimeError):
    """실제 provider 호출 hard cap을 넘기기 전에 평가를 중단한다."""


class SideEffectDetected(RuntimeError):
    """no-send/no-persistence 평가 중 side effect가 관측됐다."""


@dataclass(frozen=True)
class HistoryTurn:
    id: str
    role: str
    content: str


@dataclass(frozen=True)
class ScenarioExpected:
    context_relations: tuple[str, ...]
    selected_turn_ids: tuple[str, ...]
    clarification_required: bool
    execution_modes: tuple[str, ...]
    acceptable_assets: tuple[str, ...]
    fact_required: bool
    v4_route: str
    graph_scope: str
    evaluation_scope: str
    side_effect_policy: str
    required_terms: tuple[str, ...]


@dataclass(frozen=True)
class ScenarioCase:
    id: str
    category: str
    critical: bool
    history: tuple[HistoryTurn, ...]
    current: str
    expected: ScenarioExpected


@dataclass(frozen=True)
class SideEffectCounts:
    telegram_send: int = 0
    cron_notifier: int = 0
    conversation_write: int = 0

    @property
    def total(self) -> int:
        return self.telegram_send + self.cron_notifier + self.conversation_write


class SideEffectGuard:
    """한 건이라도 관측되면 즉시 fail-closed 하는 누적 counter."""

    def __init__(self) -> None:
        self._counts = SideEffectCounts()

    @property
    def counts(self) -> SideEffectCounts:
        return self._counts

    def observe(self, counts: SideEffectCounts) -> None:
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in (
                counts.telegram_send,
                counts.cron_notifier,
                counts.conversation_write,
            )
        ):
            raise ValueError("side-effect counts must be non-negative integers")
        self._counts = SideEffectCounts(
            telegram_send=self._counts.telegram_send + counts.telegram_send,
            cron_notifier=self._counts.cron_notifier + counts.cron_notifier,
            conversation_write=self._counts.conversation_write
            + counts.conversation_write,
        )
        if counts.total:
            raise SideEffectDetected("side_effect_detected")


class ProviderCallBudget:
    """Provider 요청을 보내기 직전에 hard cap을 원자적으로 적용한다."""

    def __init__(self, maximum: int) -> None:
        if maximum < 1:
            raise ValueError("max provider calls must be positive")
        self.maximum = maximum
        self.used = 0

    def reserve(self) -> None:
        if self.used >= self.maximum:
            raise ProviderBudgetExceeded("provider_call_budget_exhausted")
        self.used += 1


class _TrackingRouter:
    """Router의 단일 send 경계에서 실제 호출 수·사용량·attribution을 수집한다."""

    def __init__(self, router: Any, budget: ProviderCallBudget) -> None:
        self._router = router
        self._budget = budget
        self.backend = "configured-router"
        self.model = "unknown"
        self.input_tokens = 0
        self.output_tokens = 0

    async def send(self, request: Any) -> Any:
        self._budget.reserve()
        response = await self._router.send(request)
        self.backend = (
            _safe_dimension(getattr(response, "backend_name", "")) or self.backend
        )
        self.model = _safe_dimension(getattr(response, "model", "")) or self.model
        usage = getattr(response, "usage", None)
        if isinstance(usage, Mapping):
            self.input_tokens += _non_negative_int(usage.get("input_tokens"))
            self.output_tokens += _non_negative_int(usage.get("output_tokens"))
        return response


@dataclass(frozen=True)
class ContractIssue:
    asset_type: str
    asset_name: str
    error_code: str

    def to_report(self) -> dict[str, str]:
        return {
            "asset_type": self.asset_type,
            "asset_name": self.asset_name,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class ContractClassification:
    status: str
    assets: tuple[PlannerAsset, ...] = ()
    issues: tuple[ContractIssue, ...] = ()

    @property
    def asset_name(self) -> str:
        """한 자산 caller용 호환 projection. 분류는 항상 전체 identity를 본다."""
        return self.assets[0].name if self.assets else ""

    @property
    def error_code(self) -> str | None:
        return self.issues[0].error_code if self.issues else None


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    category: str
    critical: bool
    repeat_index: int
    expected_route: str
    actual_route: str
    gate_status: str
    assets: tuple[str, ...]
    checks: Mapping[str, bool]
    error_codes: tuple[str, ...]
    latency_ms: float
    input_tokens: int
    output_tokens: int
    contract_status: str
    contract_issues: tuple[ContractIssue, ...]
    connected_stop: str
    connected_required: bool
    connected_executor_kind: str
    evaluation_scope: str
    not_scored_reason: str | None
    ingress_kind: str
    planner_called: bool

    @property
    def scored(self) -> bool:
        return self.evaluation_scope == "runtime_scored"

    @property
    def passed(self) -> bool:
        return not self.error_codes and all(self.checks.values())

    def to_report(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "critical": self.critical,
            "repeat_index": self.repeat_index,
            "scored": self.scored,
            "passed": self.passed if self.scored else None,
            "expected_route": self.expected_route,
            "actual_route": self.actual_route,
            "gate_status": self.gate_status,
            "asset_names": list(self.assets),
            "failed_checks": sorted(name for name, ok in self.checks.items() if not ok),
            "error_codes": list(self.error_codes),
            "latency_ms": round(self.latency_ms, 3),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "contract_status": self.contract_status,
            "contract_issues": [item.to_report() for item in self.contract_issues],
            "connected_stop": self.connected_stop,
            "connected_required": self.connected_required,
            "connected_executor_kind": self.connected_executor_kind,
            "evaluation_scope": self.evaluation_scope,
            "not_scored_reason": self.not_scored_reason,
            "ingress_kind": self.ingress_kind,
            "planner_called": self.planner_called,
        }


ConnectedExecutor = Callable[
    [ScenarioCase, UnifiedTurnPlan, tuple[PlannerAsset, ...]],
    Awaitable[tuple[str, SideEffectCounts]],
]
PlannerCallable = Callable[..., Awaitable[UnifiedTurnPlan]]


class ConnectedContractProbe:
    """격리된 V4 graph에서 read-only contract를 synthetic executor로 검증한다."""

    def __init__(self, *, definitions: Sequence[Any], directory: str | Path) -> None:
        isolated = Path(directory).resolve()
        isolated.mkdir(parents=True, exist_ok=True)
        self._store = ConversationStore(isolated / "conversations.db")
        facade = LangGraphV4RolloutFacade(
            architecture="langgraph_v4",
            mode="shadow",
            shadow_no_send=True,
            budget=ShadowBudgetUsageV1(
                max_graph_steps=1000,
                max_asset_calls=64,
                max_llm_calls=64,
                max_tokens=200000,
                max_seconds=1200,
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
        self._runner = ConnectedShadowTurnRunner(
            facade=facade,
            definitions=definitions,
            conversation_store=self._store,
            recipe_executor=self._recipe_executor,
            skill_executor=self._skill_executor,
        )
        self._sequence = 0
        self.last_rollback_reasons: tuple[str, ...] = ()

    @staticmethod
    async def _recipe_executor(_definition: Any, _bound_steps: Any) -> dict[str, str]:
        return {"fixture_result": "connected"}

    @staticmethod
    async def _skill_executor(_definition: Any, _argv: Any) -> dict[str, str]:
        return {"operation_result": "connected"}

    async def __call__(
        self,
        case: ScenarioCase,
        plan: UnifiedTurnPlan,
        _assets: tuple[PlannerAsset, ...],
    ) -> tuple[str, SideEffectCounts]:
        self._sequence += 1
        result = await self._runner.run(
            plan=plan,
            legacy=LegacyRunTelemetryV1(
                selected_route=normalize_v4_route(plan),
                terminal_outcome=TerminalOutcome.COMPLETED,
                model_calls=1,
            ),
            request_id=f"scenario-{case.id}-{self._sequence}",
            session_key="langgraph-v4-scenario-eval",
            planner_model_calls=1,
            planner_tokens=0,
        )
        counts = result.side_effect_counts
        observed = SideEffectCounts(
            telegram_send=counts.telegram_send,
            cron_notifier=counts.notifier,
            conversation_write=counts.conversation_write,
        )
        if self._store.get_recent():
            observed = SideEffectCounts(
                telegram_send=observed.telegram_send,
                cron_notifier=observed.cron_notifier,
                conversation_write=observed.conversation_write + 1,
            )
        stop = result.telemetry.budget_usage.stop_condition
        self.last_rollback_reasons = result.comparison.rollback_reasons
        if result.comparison.rollback_required:
            stop = "rollback_required"
        return stop, observed

    def close(self) -> None:
        self._store.close()


def _strict_keys(value: Mapping[str, Any], expected: set[str], where: str) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise ScenarioFixtureError(
            f"{where} keys mismatch missing={missing} extra={extra}"
        )


def _string_tuple(
    value: Any, *, where: str, allow_empty: bool = True
) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ScenarioFixtureError(f"{where} must be a string array")
    items = tuple(item.strip() for item in value)
    if any(not item for item in items) or len(items) != len(set(items)):
        raise ScenarioFixtureError(f"{where} must contain unique non-empty strings")
    if not allow_empty and not items:
        raise ScenarioFixtureError(f"{where} must not be empty")
    return items


def _parse_case(raw: Any, line_number: int) -> ScenarioCase:
    if not isinstance(raw, Mapping):
        raise ScenarioFixtureError(f"line {line_number} must be an object")
    _strict_keys(
        raw,
        {"id", "category", "critical", "history", "current", "expected"},
        f"line {line_number}",
    )
    case_id = raw["id"]
    category = raw["category"]
    current = raw["current"]
    if not isinstance(case_id, str) or not _ID_RE.fullmatch(case_id):
        raise ScenarioFixtureError(f"line {line_number} has invalid id")
    if not isinstance(category, str) or not _ID_RE.fullmatch(
        category.replace("_", "-")
    ):
        raise ScenarioFixtureError(f"fixture {case_id} has invalid category")
    if not isinstance(raw["critical"], bool):
        raise ScenarioFixtureError(f"fixture {case_id} critical must be boolean")
    if not isinstance(current, str) or not current.strip() or len(current) > 1000:
        raise ScenarioFixtureError(f"fixture {case_id} has invalid current")
    history_raw = raw["history"]
    if not isinstance(history_raw, list) or len(history_raw) > 8:
        raise ScenarioFixtureError(
            f"fixture {case_id} history must be an array of at most 8 turns"
        )
    history: list[HistoryTurn] = []
    for index, item in enumerate(history_raw):
        if not isinstance(item, Mapping):
            raise ScenarioFixtureError(
                f"fixture {case_id} history[{index}] must be an object"
            )
        _strict_keys(
            item, {"id", "role", "content"}, f"fixture {case_id} history[{index}]"
        )
        if item["role"] not in {"user", "assistant"}:
            raise ScenarioFixtureError(f"fixture {case_id} has invalid history role")
        if not all(
            isinstance(item[key], str) and item[key].strip()
            for key in ("id", "content")
        ):
            raise ScenarioFixtureError(f"fixture {case_id} has invalid history text")
        history.append(HistoryTurn(item["id"], item["role"], item["content"]))
    history_ids = tuple(item.id for item in history)
    if len(history_ids) != len(set(history_ids)):
        raise ScenarioFixtureError(f"fixture {case_id} has duplicate history id")

    expected_raw = raw["expected"]
    if not isinstance(expected_raw, Mapping):
        raise ScenarioFixtureError(f"fixture {case_id} expected must be an object")
    expected_keys = {
        "context_relations",
        "selected_turn_ids",
        "clarification_required",
        "execution_modes",
        "acceptable_assets",
        "fact_required",
        "v4_route",
        "graph_scope",
        "evaluation_scope",
        "side_effect_policy",
        "required_terms",
    }
    _strict_keys(expected_raw, expected_keys, f"fixture {case_id} expected")
    relations = _string_tuple(
        expected_raw["context_relations"],
        where=f"fixture {case_id} context_relations",
        allow_empty=False,
    )
    modes = _string_tuple(
        expected_raw["execution_modes"],
        where=f"fixture {case_id} execution_modes",
        allow_empty=False,
    )
    selected = _string_tuple(
        expected_raw["selected_turn_ids"], where=f"fixture {case_id} selected_turn_ids"
    )
    assets = _string_tuple(
        expected_raw["acceptable_assets"], where=f"fixture {case_id} acceptable_assets"
    )
    terms = _string_tuple(
        expected_raw["required_terms"], where=f"fixture {case_id} required_terms"
    )
    if not set(relations) <= _RELATIONS or not set(modes) <= _MODES:
        raise ScenarioFixtureError(f"fixture {case_id} has unknown relation or mode")
    if not set(selected) <= set(history_ids):
        raise ScenarioFixtureError(f"fixture {case_id} selects an unknown history id")
    if not isinstance(expected_raw["clarification_required"], bool) or not isinstance(
        expected_raw["fact_required"], bool
    ):
        raise ScenarioFixtureError(f"fixture {case_id} expected booleans are invalid")
    route = expected_raw["v4_route"]
    scope = expected_raw["graph_scope"]
    evaluation_scope = expected_raw["evaluation_scope"]
    policy = expected_raw["side_effect_policy"]
    if (
        route not in V4_ROUTES
        or scope not in GRAPH_SCOPES
        or evaluation_scope not in EVALUATION_SCOPES
        or policy not in SIDE_EFFECT_POLICIES
    ):
        raise ScenarioFixtureError(
            f"fixture {case_id} has unknown route/scope/evaluation/policy"
        )
    if evaluation_scope == "ingress_bypass" and scope != "ingress_bypass":
        raise ScenarioFixtureError(
            f"fixture {case_id} ingress evaluation must use ingress graph scope"
        )
    if route in {"command_bypass", "recipe_command_bypass"} and not current.startswith(
        "/"
    ):
        raise ScenarioFixtureError(
            f"fixture {case_id} command bypass must use a slash command"
        )
    if route == "interrupt" and not expected_raw["clarification_required"]:
        raise ScenarioFixtureError(
            f"fixture {case_id} interrupt must require clarification"
        )
    return ScenarioCase(
        id=case_id,
        category=category,
        critical=raw["critical"],
        history=tuple(history),
        current=current,
        expected=ScenarioExpected(
            context_relations=relations,
            selected_turn_ids=selected,
            clarification_required=expected_raw["clarification_required"],
            execution_modes=modes,
            acceptable_assets=assets,
            fact_required=expected_raw["fact_required"],
            v4_route=route,
            graph_scope=scope,
            evaluation_scope=evaluation_scope,
            side_effect_policy=policy,
            required_terms=terms,
        ),
    )


def load_scenarios(
    path: str | Path, *, expected_count: int | None = 32
) -> tuple[ScenarioCase, ...]:
    """JSONL을 strict하게 읽고 duplicate/unknown gold를 거부한다."""
    fixture_path = Path(path)
    cases: list[ScenarioCase] = []
    for line_number, line in enumerate(
        fixture_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            raise ScenarioFixtureError(f"line {line_number} is blank")
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ScenarioFixtureError(f"line {line_number} is invalid JSON") from exc
        cases.append(_parse_case(raw, line_number))
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ScenarioFixtureError("fixture contains duplicate case id")
    if expected_count is not None and len(cases) != expected_count:
        raise ScenarioFixtureError(
            f"fixture must contain exactly {expected_count} cases"
        )
    return tuple(cases)


def context_candidates(case: ScenarioCase) -> ContextCandidateSet:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    candidates = tuple(
        ContextCandidate(
            turn_id=turn.id,
            role=turn.role,
            timestamp=now,
            content=turn.content,
            trust=ContextTrust.USER_INPUT
            if turn.role == "user"
            else ContextTrust.ASSISTANT_CONTEXT_ONLY,
        )
        for turn in case.history
    )
    return ContextCandidateSet(
        candidates, sum(len(item.content) for item in candidates), False
    )


def classify_ingress(text: str, recipe_names: Sequence[str]) -> str | None:
    """Production의 native-command→recipe-command 선행 순서를 read-only로 분류한다."""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    command = stripped[1:].split(None, 1)[0] if stripped[1:] else ""
    if command in _NATIVE_INGRESS_COMMANDS:
        return "native_command"
    if command in recipe_names:
        return "recipe_command"
    return None


def selected_asset_refs(plan: UnifiedTurnPlan) -> tuple[AssetRef, ...]:
    """Capability와 execution의 primary/supporting/allowed identity 전체를 반환한다."""
    refs = (
        (
            ()
            if plan.capability.primary_asset is None
            else (plan.capability.primary_asset,)
        )
        + plan.capability.supporting_assets
        + (
            ()
            if plan.execution.primary_asset is None
            else (plan.execution.primary_asset,)
        )
        + plan.execution.allowed_assets
    )
    return tuple({(ref.asset_type, ref.name): ref for ref in refs}.values())


def selected_assets(plan: UnifiedTurnPlan) -> tuple[str, ...]:
    """Sanitized report용 name projection. Contract 판정에는 사용하지 않는다."""
    return tuple(dict.fromkeys(ref.name for ref in selected_asset_refs(plan)))


def normalize_v4_route(
    plan: UnifiedTurnPlan | None, *, command_bypass: bool = False
) -> str:
    """Planner 출력의 mode/asset을 V4 상위 route로만 정규화한다."""
    if command_bypass:
        return "command_bypass"
    if plan is None:
        return "planner_error"
    if plan.clarification.required or plan.execution.mode is ExecutionMode.CLARIFY:
        return "interrupt"
    if plan.execution.mode is ExecutionMode.RESOLVE_COMPLEX_PROBLEM:
        return "deep_research"
    if plan.execution.mode is ExecutionMode.ANSWER_WITH_EVIDENCE:
        return "react"
    primary = plan.capability.primary_asset or plan.execution.primary_asset
    if primary is not None and primary.asset_type == "recipe":
        return "recipe"
    if primary is not None:
        return "react"
    return "simple_conversation"


def classify_contract(
    catalog: PlannerCatalog,
    asset_refs: Sequence[AssetRef],
) -> ContractClassification:
    """선택된 모든 exact identity가 complete read-only일 때만 실행을 허용한다."""
    if not asset_refs:
        return ContractClassification("not_applicable")
    by_identity = {(asset.asset_type, asset.name): asset for asset in catalog.assets}
    assets: list[PlannerAsset] = []
    issues: list[ContractIssue] = []
    for ref in asset_refs:
        asset = by_identity.get((ref.asset_type, ref.name))
        if asset is None:
            issues.append(
                ContractIssue(ref.asset_type, ref.name, "contract.asset_missing")
            )
            continue
        assets.append(asset)
        complete = all(
            (
                asset.declared,
                asset.runtime_visible,
                asset.coverage == "full_coverage",
                bool(asset.input_contract_ref or asset.input_contract),
                bool(asset.output_contract_ref or asset.output_contract),
            )
        )
        if not complete:
            issues.append(
                ContractIssue(asset.asset_type, asset.name, "contract.incomplete")
            )
        elif not asset.read_only or asset.side_effects or asset.requires_confirmation:
            issues.append(
                ContractIssue(asset.asset_type, asset.name, "contract.not_read_only")
            )
    if any(item.error_code != "contract.not_read_only" for item in issues):
        status = "contract_coverage_gap"
    elif issues:
        status = "dispatch_denied"
    else:
        status = "read_only_complete"
    return ContractClassification(status, tuple(assets), tuple(issues))


def _desired_gate(case: ScenarioCase) -> frozenset[str]:
    if case.expected.v4_route != "interrupt":
        return frozenset({GateStatus.PASS.value})
    if case.expected.side_effect_policy == "confirmation_before_dispatch":
        return frozenset(
            {GateStatus.CONFIRMATION_REQUIRED.value, GateStatus.CLARIFY.value}
        )
    return frozenset({GateStatus.CLARIFY.value, GateStatus.CONFIRMATION_REQUIRED.value})


def score_plan(
    case: ScenarioCase,
    plan: UnifiedTurnPlan,
    gate: PlanGateResult,
    catalog: PlannerCatalog,
    *,
    repeat_index: int = 1,
    latency_ms: float = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    contract_classification: ContractClassification | None = None,
    connected_stop: str = "not_run",
    connected_executor_kind: str = "none",
) -> CaseResult:
    route = normalize_v4_route(plan)
    asset_refs = selected_asset_refs(plan)
    assets = selected_assets(plan)
    standalone = plan.context.standalone_question.casefold()
    selected = tuple(plan.context.selected_turn_ids)
    checks = {
        "route": route == case.expected.v4_route,
        "gate": gate.status.value in _desired_gate(case),
        "context_relation": plan.context.relation.value
        in case.expected.context_relations,
        "selected_turn_ids": selected == case.expected.selected_turn_ids,
        "clarification": plan.clarification.required
        == case.expected.clarification_required,
        "execution_mode": plan.execution.mode.value in case.expected.execution_modes,
        "asset": bool(set(assets) & set(case.expected.acceptable_assets))
        if case.expected.acceptable_assets
        else not assets,
        "fact_required": plan.fact_check.required == case.expected.fact_required,
        "required_terms": all(
            term.casefold() in standalone for term in case.expected.required_terms
        ),
        "mutation_pre_dispatch": not (
            case.expected.side_effect_policy == "confirmation_before_dispatch"
            and route != "interrupt"
        ),
    }
    classification = contract_classification or classify_contract(
        catalog,
        asset_refs,
    )
    connected_required = (
        classification.status == "read_only_complete"
        and gate.status is GateStatus.PASS
        and case.expected.graph_scope
        not in {"planner_only", "interrupt_only", "ingress_bypass"}
    )
    errors = (
        ()
        if gate.status.value in _desired_gate(case)
        else tuple(item.code for item in gate.violations)
    )
    return CaseResult(
        case_id=case.id,
        category=case.category,
        critical=case.critical,
        repeat_index=repeat_index,
        expected_route=case.expected.v4_route,
        actual_route=route,
        gate_status=gate.status.value,
        assets=assets,
        checks=checks,
        error_codes=errors,
        latency_ms=max(0.0, float(latency_ms)),
        input_tokens=max(0, int(input_tokens)),
        output_tokens=max(0, int(output_tokens)),
        contract_status=classification.status,
        contract_issues=classification.issues,
        connected_stop=connected_stop,
        connected_required=connected_required,
        connected_executor_kind=connected_executor_kind,
        evaluation_scope=case.expected.evaluation_scope,
        not_scored_reason=None,
        ingress_kind="none",
        planner_called=True,
    )


def not_scored_result(
    case: ScenarioCase,
    *,
    ingress_kind: str = "none",
) -> CaseResult:
    """Inventory는 보존하되 runtime planner 품질 분모에서 제외한다."""
    if case.expected.evaluation_scope == "runtime_scored":
        raise ValueError("runtime-scored case cannot become not-scored")
    actual_route = case.expected.v4_route
    error_codes: tuple[str, ...] = ()
    if case.expected.evaluation_scope == "ingress_bypass":
        if ingress_kind == "recipe_command":
            actual_route = "recipe_command_bypass"
        elif ingress_kind == "native_command":
            actual_route = "command_bypass"
        else:
            actual_route = "ingress_unclassified"
            error_codes = ("benchmark.ingress_classifier_mismatch",)
        if not error_codes and actual_route != case.expected.v4_route:
            error_codes = ("benchmark.ingress_route_mismatch",)
    return CaseResult(
        case_id=case.id,
        category=case.category,
        critical=case.critical,
        repeat_index=1,
        expected_route=case.expected.v4_route,
        actual_route=actual_route,
        gate_status="not_scored",
        assets=(),
        checks={},
        error_codes=error_codes,
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        contract_status="not_applicable",
        contract_issues=(),
        connected_stop="not_scored",
        connected_required=False,
        connected_executor_kind="none",
        evaluation_scope=case.expected.evaluation_scope,
        not_scored_reason=case.expected.evaluation_scope,
        ingress_kind=ingress_kind,
        planner_called=False,
    )


def _failure_result(
    case: ScenarioCase, repeat_index: int, code: str, latency_ms: float
) -> CaseResult:
    return CaseResult(
        case_id=case.id,
        category=case.category,
        critical=case.critical,
        repeat_index=repeat_index,
        expected_route=case.expected.v4_route,
        actual_route="planner_error",
        gate_status="not_run",
        assets=(),
        checks={"schema": False},
        error_codes=(code,),
        latency_ms=latency_ms,
        input_tokens=0,
        output_tokens=0,
        contract_status="not_evaluated",
        contract_issues=(),
        connected_stop="not_run",
        connected_required=False,
        connected_executor_kind="none",
        evaluation_scope=case.expected.evaluation_scope,
        not_scored_reason=None,
        ingress_kind="none",
        planner_called=True,
    )


def aggregate_results(
    results: Sequence[CaseResult],
    *,
    provider_calls: int,
    provider_call_budget: int,
    provider_backend: str,
    provider_model: str,
    side_effect_counts: SideEffectCounts,
    elapsed_seconds: float,
) -> dict[str, Any]:
    rows = list(results)
    scored_rows = [row for row in rows if row.scored]
    not_scored_rows = [row for row in rows if not row.scored]
    categories: dict[str, dict[str, Any]] = {}
    for category in sorted({row.category for row in rows}):
        inventory = [row for row in rows if row.category == category]
        selected = [row for row in inventory if row.scored]
        categories[category] = {
            "inventory_runs": len(inventory),
            "scored_runs": len(selected),
            "not_scored_runs": len(inventory) - len(selected),
            "pass_rate": (
                sum(row.passed for row in selected) / len(selected)
                if selected
                else None
            ),
        }
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    for row in scored_rows:
        confusion[row.expected_route][row.actual_route] += 1
    by_case: dict[str, list[CaseResult]] = defaultdict(list)
    for row in rows:
        by_case[row.case_id].append(row)
    scored_by_case: dict[str, list[CaseResult]] = defaultdict(list)
    for row in scored_rows:
        scored_by_case[row.case_id].append(row)
    critical_ids = sorted({row.case_id for row in scored_rows if row.critical})
    stable = {
        case_id: len(
            {
                (row.actual_route, row.gate_status, row.assets)
                for row in scored_by_case[case_id]
            }
        )
        == 1
        for case_id in critical_ids
    }
    contract_gaps = Counter(
        issue.asset_name
        for row in scored_rows
        for issue in row.contract_issues
        if issue.error_code != "contract.not_read_only"
    )
    contract_denials = Counter(
        issue.asset_name
        for row in scored_rows
        for issue in row.contract_issues
        if issue.error_code == "contract.not_read_only"
    )
    contract_gap_count = sum(
        issue.error_code != "contract.not_read_only"
        for row in scored_rows
        for issue in row.contract_issues
    )
    connected_required = [row for row in scored_rows if row.connected_required]
    connected_completed = [
        row for row in connected_required if row.connected_stop == "completed"
    ]
    rollback_count = sum(
        row.connected_stop == "rollback_required" for row in scored_rows
    )
    route_mode_context = [
        row.checks.get("route", False)
        and row.checks.get("execution_mode", False)
        and row.checks.get("context_relation", False)
        for row in scored_rows
    ]
    not_scored_inventory = Counter(
        row.not_scored_reason or "unspecified" for row in not_scored_rows
    )
    inventory_fidelity_error_count = sum(
        bool(row.error_codes) for row in not_scored_rows
    )
    executor_kinds = sorted(
        {
            row.connected_executor_kind
            for row in scored_rows
            if row.connected_executor_kind != "none"
        }
    )
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "decision": "go",
        "summary": {
            "runs": len(scored_rows),
            "unique_cases": len(scored_by_case),
            "total_runs": len(rows),
            "total_inventory_cases": len(by_case),
            "scored_runs": len(scored_rows),
            "scored_cases": len(scored_by_case),
            "not_scored_runs": len(not_scored_rows),
            "not_scored_cases": len({row.case_id for row in not_scored_rows}),
            "schema_validity_rate": sum(
                "schema" not in row.checks or row.checks["schema"]
                for row in scored_rows
            )
            / len(scored_rows)
            if scored_rows
            else 0.0,
            "pass_rate": sum(row.passed for row in scored_rows) / len(scored_rows)
            if scored_rows
            else 0.0,
            "route_mode_context_macro_pass_rate": sum(route_mode_context)
            / len(route_mode_context)
            if route_mode_context
            else 0.0,
            "critical_pass_rate": sum(row.passed for row in scored_rows if row.critical)
            / sum(row.critical for row in scored_rows)
            if any(row.critical for row in scored_rows)
            else None,
            "critical_stability_rate": sum(stable.values()) / len(stable)
            if stable
            else None,
            "inventory_fidelity_error_count": inventory_fidelity_error_count,
            "contract_gap_count": contract_gap_count,
            "connected_required_count": len(connected_required),
            "connected_completed_count": len(connected_completed),
            "rollback_required_count": rollback_count,
            "latency_ms": {
                "average": sum(row.latency_ms for row in scored_rows) / len(scored_rows)
                if scored_rows
                else 0.0,
                "maximum": max((row.latency_ms for row in scored_rows), default=0.0),
            },
            "tokens": {
                "input_total": sum(row.input_tokens for row in scored_rows),
                "output_total": sum(row.output_tokens for row in scored_rows),
            },
            "provider_calls": provider_calls,
            "provider_call_budget": provider_call_budget,
            "elapsed_seconds": round(max(0.0, elapsed_seconds), 3),
        },
        "provider": {
            "backend": _safe_dimension(provider_backend),
            "model": _safe_dimension(provider_model),
        },
        "categories": categories,
        "route_confusion": {
            expected: dict(sorted(actual.items()))
            for expected, actual in sorted(confusion.items())
        },
        "critical_stability": stable,
        "not_scored_inventory": dict(sorted(not_scored_inventory.items())),
        "connected_executor_kinds": executor_kinds,
        "contract_gaps": dict(sorted(contract_gaps.items())),
        "contract_denials": dict(sorted(contract_denials.items())),
        "side_effect_counts": {
            "telegram_send": side_effect_counts.telegram_send,
            "cron_notifier": side_effect_counts.cron_notifier,
            "conversation_write": side_effect_counts.conversation_write,
        },
        "cases": [row.to_report() for row in rows],
    }
    summary = report["summary"]
    if not (
        summary["schema_validity_rate"] == 1.0
        and summary["route_mode_context_macro_pass_rate"] >= 0.9
        and summary["critical_pass_rate"] == 1.0
        and summary["critical_stability_rate"] == 1.0
        and summary["inventory_fidelity_error_count"] == 0
        and summary["contract_gap_count"] == 0
        and summary["rollback_required_count"] == 0
        and summary["connected_completed_count"] == summary["connected_required_count"]
        and side_effect_counts.total == 0
    ):
        report["decision"] = "hold"
    assert_sanitized_report(report)
    return report


def assert_sanitized_report(report: Mapping[str, Any]) -> None:
    """Report가 원문/query/credential 경계를 우회하지 못하게 재귀 검증한다."""

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if str(key).casefold() in _FORBIDDEN_REPORT_KEYS:
                    raise ValueError(f"forbidden report field: {key}")
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str) and _SECRET_RE.search(value):
            raise ValueError("credential-like text in report")

    visit(report)


def render_markdown(report: Mapping[str, Any]) -> str:
    """Sanitized JSON만 입력으로 받아 운영 분석 Markdown을 만든다."""
    assert_sanitized_report(report)
    summary = report["summary"]
    counts = report["side_effect_counts"]
    lines = [
        "# LangGraph V4 사용자 시나리오 평가",
        "",
        f"- 판단: **{str(report['decision']).upper()}**",
        f"- inventory: {summary['total_inventory_cases']}개 case / {summary['total_runs']}개 row",
        f"- runtime scored: {summary['scored_cases']}개 case / {summary['scored_runs']}회",
        f"- not scored: {summary['not_scored_cases']}개 case / {summary['not_scored_runs']}회",
        f"- route+mode+context macro pass: {summary['route_mode_context_macro_pass_rate']:.1%}",
        f"- critical pass / stability: {summary['critical_pass_rate']:.1%} / {summary['critical_stability_rate']:.1%}",
        f"- contract gaps: {summary['contract_gap_count']}",
        f"- connected completion: {summary['connected_completed_count']}/{summary['connected_required_count']}",
        f"- rollback required: {summary['rollback_required_count']}",
        f"- connected executor: {', '.join(report['connected_executor_kinds']) or 'none'}",
        f"- provider calls: {summary['provider_calls']}/{summary['provider_call_budget']}",
        f"- Telegram / cron / ConversationStore: {counts['telegram_send']}/{counts['cron_notifier']}/{counts['conversation_write']}",
        "",
        "## Category",
        "",
        "| category | inventory | scored | not scored | pass |",
        "|---|---:|---:|---:|---:|",
    ]
    for category, values in report["categories"].items():
        pass_rate = (
            f"{values['pass_rate']:.1%}" if values["pass_rate"] is not None else "N/A"
        )
        lines.append(
            f"| {category} | {values['inventory_runs']} | "
            f"{values['scored_runs']} | {values['not_scored_runs']} | {pass_rate} |"
        )
    lines.extend(["", "## Not-scored inventory", ""])
    for reason, count in report["not_scored_inventory"].items():
        lines.append(f"- `{reason}`: {count}")
    lines.extend(["", "## Contract coverage gaps", ""])
    gaps = report["contract_gaps"]
    if gaps:
        lines.extend(f"- `{name}`: {count}" for name, count in gaps.items())
    else:
        lines.append("- 없음")
    failures = [
        row for row in report["cases"] if row["scored"] and row["passed"] is False
    ]
    lines.extend(["", "## Failures", ""])
    if failures:
        for row in failures:
            codes = sorted(set(row["failed_checks"] + row["error_codes"]))
            lines.append(
                f"- {row['case_id']} repeat={row['repeat_index']}: {', '.join(codes)}"
            )
    else:
        lines.append("- 없음")
    return "\n".join(lines) + "\n"


class ScenarioEvaluator:
    """Configured planner를 budget/deadline/no-side-effect 경계 안에서 실행한다."""

    def __init__(
        self,
        *,
        catalog: PlannerCatalog,
        router: Any,
        planner: PlannerCallable = plan_turn_with_llm,
        max_provider_calls: int = 64,
        deadline_seconds: float = 1200,
        execute_read_only_contract_assets: bool = False,
        connected_executor: ConnectedExecutor | None = None,
        connected_executor_kind: str = "none",
        ingress_recipe_names: Sequence[str] = (),
    ) -> None:
        if deadline_seconds <= 0:
            raise ValueError("deadline must be positive")
        self.catalog = catalog
        self.budget = ProviderCallBudget(max_provider_calls)
        self.router = _TrackingRouter(router, self.budget)
        self.planner = planner
        self.deadline_seconds = deadline_seconds
        self.execute_read_only_contract_assets = execute_read_only_contract_assets
        self.connected_executor = connected_executor
        self.connected_executor_kind = _safe_dimension(connected_executor_kind)
        self.ingress_recipe_names = frozenset(ingress_recipe_names)
        self.guard = SideEffectGuard()

    async def evaluate(
        self, cases: Sequence[ScenarioCase], *, repeat_critical: int = 3
    ) -> dict[str, Any]:
        if repeat_critical < 1:
            raise ValueError("repeat-critical must be positive")
        started = time.monotonic()
        rows: list[CaseResult] = []
        consecutive_failures = 0
        total_failures = 0
        schedule = [(case, 1) for case in cases]
        for repeat_index in range(2, repeat_critical + 1):
            schedule.extend(
                (case, repeat_index)
                for case in cases
                if case.critical and case.expected.evaluation_scope == "runtime_scored"
            )
        async with asyncio.timeout(self.deadline_seconds):
            for case, repeat_index in schedule:
                if case.expected.evaluation_scope != "runtime_scored":
                    ingress_kind = (
                        classify_ingress(case.current, self.ingress_recipe_names)
                        if case.expected.evaluation_scope == "ingress_bypass"
                        else "none"
                    )
                    rows.append(
                        not_scored_result(case, ingress_kind=ingress_kind or "none")
                    )
                    continue
                call_started = time.monotonic()
                before_input = self.router.input_tokens
                before_output = self.router.output_tokens
                try:
                    plan = await self.planner(
                        case.current,
                        candidates=context_candidates(case),
                        catalog=self.catalog,
                        router=self.router,
                        max_tokens=2048,
                        reasoning={"enabled": True, "effort": "low"},
                    )
                    gate = PlanGate().evaluate(
                        plan, candidates=context_candidates(case), catalog=self.catalog
                    )
                    classification = classify_contract(
                        self.catalog,
                        selected_asset_refs(plan),
                    )
                    connected_stop = "not_run"
                    if (
                        self.execute_read_only_contract_assets
                        and classification.status == "read_only_complete"
                        and self.connected_executor is not None
                        and gate.status is GateStatus.PASS
                        and case.expected.graph_scope
                        not in {"planner_only", "interrupt_only", "ingress_bypass"}
                    ):
                        connected_stop, observed = await self.connected_executor(
                            case,
                            plan,
                            classification.assets,
                        )
                        self.guard.observe(observed)
                    rows.append(
                        score_plan(
                            case,
                            plan,
                            gate,
                            self.catalog,
                            repeat_index=repeat_index,
                            latency_ms=(time.monotonic() - call_started) * 1000,
                            input_tokens=self.router.input_tokens - before_input,
                            output_tokens=self.router.output_tokens - before_output,
                            contract_classification=classification,
                            connected_stop=connected_stop,
                            connected_executor_kind=self.connected_executor_kind,
                        )
                    )
                    consecutive_failures = 0
                except (PlannerUnavailable, ValueError, TypeError):
                    code = "planner.schema_or_unavailable"
                    rows.append(
                        _failure_result(
                            case,
                            repeat_index,
                            code,
                            (time.monotonic() - call_started) * 1000,
                        )
                    )
                    consecutive_failures += 1
                    total_failures += 1
                    if consecutive_failures >= 3 or total_failures >= 10:
                        break
        return aggregate_results(
            rows,
            provider_calls=self.budget.used,
            provider_call_budget=self.budget.maximum,
            provider_backend=self.router.backend,
            provider_model=self.router.model,
            side_effect_counts=self.guard.counts,
            elapsed_seconds=time.monotonic() - started,
        )


def _non_negative_int(value: Any) -> int:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else 0
    )


def _safe_dimension(value: Any) -> str:
    return _SAFE_DIMENSION_RE.sub("_", str(value or ""))[:96]
