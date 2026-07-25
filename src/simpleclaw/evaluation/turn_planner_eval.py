"""Unified TurnPlanner의 fixed-gold 품질을 오프라인에서 평가한다.

이 모듈은 planner 실행과 채점을 분리한다. 호출자는 live planner 결과나 저장된
replay prediction을 같은 ``score_prediction`` 계약에 넘길 수 있다. report에는
case ID, boolean check, 지연·토큰·문맥 축소 지표만 남기고 대화 원문, standalone
질문, 검색 query, credential 값은 절대 직렬화하지 않는다.
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_CONTEXT_RELATIONS = frozenset(
    {"standalone", "same_thread", "related_reference", "topic_shift", "unclear"}
)
_EXECUTION_MODES = frozenset(
    {
        "clarify",
        "direct_answer",
        "execute_asset",
        "tool_loop",
        "fact_check",
        "complex_fact",
        "recipe",
    }
)
_MESSAGE_ROLES = frozenset({"user", "assistant"})


class FixtureFormatError(ValueError):
    """Fixture 문법이나 의미 계약이 유효하지 않을 때 발생한다."""


@dataclass(frozen=True)
class FixtureMessage:
    """ID가 있는 단일 대화 후보를 표현한다."""

    id: str
    role: str
    content: str


@dataclass(frozen=True)
class GoldExpectation:
    """Planner prediction을 채점할 fixed-gold 기대값이다."""

    context_relation: str
    selected_turn_ids: tuple[str, ...]
    clarification_required: bool
    execution_mode: str
    acceptable_assets: tuple[str, ...]
    fact_required: bool
    domains: tuple[str, ...]
    entities: tuple[str, ...]
    entity_aliases: Mapping[str, tuple[str, ...]]
    normalized_terms: tuple[tuple[str, ...], ...]
    forbidden_query_terms: tuple[str, ...]


@dataclass(frozen=True)
class TurnPlannerFixture:
    """원문과 gold, 선택적 deterministic replay를 묶은 평가 case다."""

    id: str
    history: tuple[FixtureMessage, ...]
    current: str
    gold: GoldExpectation
    critical: bool = False
    prediction: Mapping[str, Any] = field(default_factory=dict)
    metrics: Mapping[str, int | float] = field(default_factory=dict)


@dataclass(frozen=True)
class CaseEvaluation:
    """대화 원문을 보유하지 않는 단일 prediction 평가 결과다."""

    case_id: str
    relation: str
    critical: bool
    repeat_index: int
    schema_valid: bool
    checks: Mapping[str, bool]
    selected_turn_precision: float
    selected_turn_recall: float
    latency_ms: float
    input_tokens: int
    output_tokens: int
    context_reduction_rate: float | None
    error_codes: tuple[str, ...] = ()

    @property
    def macro_score(self) -> float:
        """적용된 boolean check의 단순 평균을 반환한다."""
        return (
            sum(bool(value) for value in self.checks.values()) / len(self.checks)
            if self.checks
            else 0.0
        )

    @property
    def passed(self) -> bool:
        """schema와 모든 semantic check가 통과했는지 반환한다."""
        return self.schema_valid and all(self.checks.values())

    def to_report(self) -> dict[str, Any]:
        """raw prediction을 제외한 안전한 report row를 만든다."""
        return {
            "case_id": self.case_id,
            "relation": self.relation,
            "critical": self.critical,
            "repeat_index": self.repeat_index,
            "schema_valid": self.schema_valid,
            "passed": self.passed,
            "macro_score": self.macro_score,
            "checks": dict(sorted(self.checks.items())),
            "selected_turn_precision": self.selected_turn_precision,
            "selected_turn_recall": self.selected_turn_recall,
            "latency_ms": self.latency_ms,
            "tokens": {
                "input": self.input_tokens,
                "output": self.output_tokens,
            },
            "context_reduction_rate": self.context_reduction_rate,
            "error_codes": list(self.error_codes),
        }


def _require_mapping(
    value: object,
    *,
    field_name: str,
    source: str,
    line_number: int,
) -> Mapping[str, Any]:
    """중첩 object 계약을 검사하되 원문 값은 오류에 포함하지 않는다."""
    if not isinstance(value, Mapping):
        raise FixtureFormatError(
            f"{source} line {line_number}: {field_name} must be an object"
        )
    return value


def _require_string(
    value: object,
    *,
    field_name: str,
    source: str,
    line_number: int,
) -> str:
    """필수 non-empty string을 검사한다."""
    if not isinstance(value, str) or not value.strip():
        raise FixtureFormatError(
            f"{source} line {line_number}: {field_name} must be a non-empty string"
        )
    return value


def _string_tuple(
    value: object,
    *,
    field_name: str,
    source: str,
    line_number: int,
) -> tuple[str, ...]:
    """JSON string array를 tuple로 바꾸며 shape를 검증한다."""
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise FixtureFormatError(
            f"{source} line {line_number}: {field_name} must be a string array"
        )
    return tuple(value)


def _parse_normalized_terms(
    value: object,
    *,
    source: str,
    line_number: int,
) -> tuple[tuple[str, ...], ...]:
    """각 필수 term을 alias 대안 묶음으로 정규화한다."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise FixtureFormatError(
            f"{source} line {line_number}: gold.normalized_terms must be an array"
        )
    groups: list[tuple[str, ...]] = []
    for index, item in enumerate(value):
        if isinstance(item, str) and item.strip():
            groups.append((item,))
            continue
        if isinstance(item, list) and item and all(
            isinstance(alias, str) and alias.strip() for alias in item
        ):
            groups.append(tuple(item))
            continue
        raise FixtureFormatError(
            f"{source} line {line_number}: "
            f"gold.normalized_terms[{index}] must be a string or string array"
        )
    return tuple(groups)


def parse_fixture(
    row: Mapping[str, Any],
    *,
    source: str,
    line_number: int,
) -> TurnPlannerFixture:
    """단일 JSON object를 검증된 fixture로 변환한다."""
    fixture_id = _require_string(
        row.get("id"),
        field_name="id",
        source=source,
        line_number=line_number,
    )
    current = _require_string(
        row.get("current"),
        field_name="current",
        source=source,
        line_number=line_number,
    )
    history_raw = row.get("history")
    if not isinstance(history_raw, list):
        raise FixtureFormatError(
            f"{source} line {line_number}: history must be an array"
        )
    history: list[FixtureMessage] = []
    message_ids: set[str] = set()
    for index, item in enumerate(history_raw):
        message = _require_mapping(
            item,
            field_name=f"history[{index}]",
            source=source,
            line_number=line_number,
        )
        message_id = _require_string(
            message.get("id"),
            field_name=f"history[{index}].id",
            source=source,
            line_number=line_number,
        )
        if message_id in message_ids:
            raise FixtureFormatError(
                f"{source} line {line_number}: duplicate history id {message_id}"
            )
        role = _require_string(
            message.get("role"),
            field_name=f"history[{index}].role",
            source=source,
            line_number=line_number,
        )
        if role not in _MESSAGE_ROLES:
            raise FixtureFormatError(
                f"{source} line {line_number}: unsupported history role {role}"
            )
        content = _require_string(
            message.get("content"),
            field_name=f"history[{index}].content",
            source=source,
            line_number=line_number,
        )
        message_ids.add(message_id)
        history.append(FixtureMessage(id=message_id, role=role, content=content))

    gold_raw = _require_mapping(
        row.get("gold"),
        field_name="gold",
        source=source,
        line_number=line_number,
    )
    relation = _require_string(
        gold_raw.get("context_relation"),
        field_name="gold.context_relation",
        source=source,
        line_number=line_number,
    )
    if relation not in _CONTEXT_RELATIONS:
        raise FixtureFormatError(
            f"{source} line {line_number}: unsupported context relation {relation}"
        )
    selected_turn_ids = _string_tuple(
        gold_raw.get("selected_turn_ids"),
        field_name="gold.selected_turn_ids",
        source=source,
        line_number=line_number,
    )
    missing_ids = set(selected_turn_ids) - message_ids
    if missing_ids:
        missing = ", ".join(sorted(missing_ids))
        raise FixtureFormatError(
            f"{source} line {line_number}: selected IDs absent from history: {missing}"
        )
    if relation in {"standalone", "topic_shift"} and selected_turn_ids:
        raise FixtureFormatError(
            f"{source} line {line_number}: {relation} cannot select history"
        )
    mode = _require_string(
        gold_raw.get("execution_mode"),
        field_name="gold.execution_mode",
        source=source,
        line_number=line_number,
    )
    if mode not in _EXECUTION_MODES:
        raise FixtureFormatError(
            f"{source} line {line_number}: unsupported execution mode {mode}"
        )

    aliases_raw = gold_raw.get("entity_aliases", {})
    aliases_mapping = _require_mapping(
        aliases_raw,
        field_name="gold.entity_aliases",
        source=source,
        line_number=line_number,
    )
    entity_aliases: dict[str, tuple[str, ...]] = {}
    for canonical, aliases in aliases_mapping.items():
        if not isinstance(canonical, str) or not canonical.strip():
            raise FixtureFormatError(
                f"{source} line {line_number}: entity alias key must be a string"
            )
        entity_aliases[canonical] = _string_tuple(
            aliases,
            field_name=f"gold.entity_aliases.{canonical}",
            source=source,
            line_number=line_number,
        )

    clarification_required = gold_raw.get("clarification_required")
    fact_required = gold_raw.get("fact_required")
    if not isinstance(clarification_required, bool):
        raise FixtureFormatError(
            f"{source} line {line_number}: "
            "gold.clarification_required must be boolean"
        )
    if not isinstance(fact_required, bool):
        raise FixtureFormatError(
            f"{source} line {line_number}: gold.fact_required must be boolean"
        )
    metrics = row.get("metrics", {})
    metrics_mapping = _require_mapping(
        metrics,
        field_name="metrics",
        source=source,
        line_number=line_number,
    )
    prediction = row.get("prediction", {})
    prediction_mapping = _require_mapping(
        prediction,
        field_name="prediction",
        source=source,
        line_number=line_number,
    )
    return TurnPlannerFixture(
        id=fixture_id,
        critical=bool(row.get("critical", False)),
        history=tuple(history),
        current=current,
        gold=GoldExpectation(
            context_relation=relation,
            selected_turn_ids=selected_turn_ids,
            clarification_required=clarification_required,
            execution_mode=mode,
            acceptable_assets=_string_tuple(
                gold_raw.get("acceptable_assets", []),
                field_name="gold.acceptable_assets",
                source=source,
                line_number=line_number,
            ),
            fact_required=fact_required,
            domains=_string_tuple(
                gold_raw.get("domains", []),
                field_name="gold.domains",
                source=source,
                line_number=line_number,
            ),
            entities=_string_tuple(
                gold_raw.get("entities", []),
                field_name="gold.entities",
                source=source,
                line_number=line_number,
            ),
            entity_aliases=entity_aliases,
            normalized_terms=_parse_normalized_terms(
                gold_raw.get("normalized_terms"),
                source=source,
                line_number=line_number,
            ),
            forbidden_query_terms=_string_tuple(
                gold_raw.get("forbidden_query_terms", []),
                field_name="gold.forbidden_query_terms",
                source=source,
                line_number=line_number,
            ),
        ),
        prediction=prediction_mapping,
        metrics={
            str(key): value
            for key, value in metrics_mapping.items()
            if isinstance(value, (int, float))
        },
    )


def load_fixtures(path: str | Path) -> list[TurnPlannerFixture]:
    """UTF-8 JSONL fixture를 순서대로 읽고 중복 ID를 거부한다."""
    fixture_path = Path(path)
    fixtures: list[TurnPlannerFixture] = []
    seen_ids: set[str] = set()
    try:
        lines = fixture_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise FixtureFormatError(f"cannot read fixture: {fixture_path}") from exc
    for line_number, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise FixtureFormatError(
                f"{fixture_path} line {line_number}: invalid JSON"
            ) from exc
        if not isinstance(row, Mapping):
            raise FixtureFormatError(
                f"{fixture_path} line {line_number}: fixture must be an object"
            )
        fixture = parse_fixture(
            row,
            source=str(fixture_path),
            line_number=line_number,
        )
        if fixture.id in seen_ids:
            raise FixtureFormatError(
                f"{fixture_path} line {line_number}: duplicate fixture id {fixture.id}"
            )
        seen_ids.add(fixture.id)
        fixtures.append(fixture)
    if not fixtures:
        raise FixtureFormatError(f"fixture has no cases: {fixture_path}")
    return fixtures


def _is_string_array(value: object) -> bool:
    """빈 배열을 허용하는 non-empty string array인지 반환한다."""
    return isinstance(value, list) and all(
        isinstance(item, str) and bool(item.strip()) for item in value
    )


def _is_primary_asset(value: object) -> bool:
    """no-asset 또는 exact skill/recipe asset object인지 반환한다."""
    if value is None or value == "__none__":
        return True
    if not isinstance(value, Mapping):
        return False
    return (
        set(value) == {"asset_type", "name"}
        and value.get("asset_type") in {"skill", "recipe"}
        and isinstance(value.get("name"), str)
        and bool(value["name"].strip())
    )


def _prediction_shape_errors(
    prediction: Mapping[str, Any],
    *,
    history_ids: frozenset[str],
) -> tuple[str, ...]:
    """UnifiedTurnPlan의 evaluator 소비 필드를 fail-closed 검증한다."""
    errors: list[str] = []
    for field_name in ("context", "clarification", "fact_check", "execution"):
        if not isinstance(prediction.get(field_name), Mapping):
            errors.append(f"missing:{field_name}")
    if errors:
        return tuple(errors)
    context = prediction["context"]
    clarification = prediction["clarification"]
    fact_check = prediction["fact_check"]
    execution = prediction["execution"]
    relation = context.get("relation")
    if relation not in _CONTEXT_RELATIONS:
        errors.append("invalid:context.relation")
    selected_ids = context.get("selected_turn_ids")
    if (
        not _is_string_array(selected_ids)
        or not set(selected_ids).issubset(history_ids)
    ):
        errors.append("invalid:context.selected_turn_ids")
    standalone = context.get("standalone_question")
    if not isinstance(standalone, str) or not standalone.strip():
        errors.append("invalid:context.standalone_question")
    if not isinstance(clarification.get("required"), bool):
        errors.append("invalid:clarification.required")
    if not _is_string_array(prediction.get("domains")):
        errors.append("invalid:domains")
    if not isinstance(fact_check.get("required"), bool):
        errors.append("invalid:fact_check.required")
    if (
        not isinstance(fact_check.get("domain"), str)
        or not fact_check["domain"].strip()
    ):
        errors.append("invalid:fact_check.domain")
    if not _is_string_array(fact_check.get("entities")):
        errors.append("invalid:fact_check.entities")
    if not isinstance(fact_check.get("search_query"), str):
        errors.append("invalid:fact_check.search_query")
    if execution.get("mode") not in _EXECUTION_MODES:
        errors.append("invalid:execution.mode")
    if (
        "primary_asset" not in execution
        or not _is_primary_asset(execution["primary_asset"])
    ):
        errors.append("invalid:execution.primary_asset")
    return tuple(errors)


def _normalized(value: object) -> str:
    """대소문자·공백 차이를 제거한 비교 문자열을 만든다."""
    return "".join(str(value).casefold().split())


def _contains_any(text: object, alternatives: Sequence[str]) -> bool:
    """정규화된 text에 alias 대안 중 하나가 포함되는지 검사한다."""
    normalized_text = _normalized(text)
    return any(_normalized(alternative) in normalized_text for alternative in alternatives)


def _selected_metrics(
    predicted: Sequence[str],
    gold: Sequence[str],
) -> tuple[float, float]:
    """selected-turn ID의 precision/recall을 빈 집합까지 정의한다."""
    predicted_set = set(predicted)
    gold_set = set(gold)
    precision = (
        len(predicted_set & gold_set) / len(predicted_set)
        if predicted_set
        else 1.0
    )
    recall = (
        len(predicted_set & gold_set) / len(gold_set) if gold_set else 1.0
    )
    return precision, recall


def _asset_name(execution: Mapping[str, Any]) -> str:
    """검증된 primary_asset object/sentinel에서 이름을 추출한다."""
    primary = execution.get("primary_asset")
    if primary in (None, "__none__"):
        return ""
    if isinstance(primary, Mapping):
        return str(primary["name"])
    return ""


def _context_reduction(
    fixture: TurnPlannerFixture,
    selected_ids: Sequence[str],
) -> float | None:
    """전체 후보 대비 downstream 선택 문맥의 문자 축소율을 계산한다."""
    candidate_chars = sum(len(message.content) for message in fixture.history)
    if candidate_chars == 0:
        return None
    selected_set = set(selected_ids)
    selected_chars = sum(
        len(message.content)
        for message in fixture.history
        if message.id in selected_set
    )
    return max(0.0, min(1.0, 1.0 - (selected_chars / candidate_chars)))


def score_prediction(
    fixture: TurnPlannerFixture,
    prediction: Mapping[str, Any],
    *,
    latency_ms: float = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    repeat_index: int = 1,
) -> CaseEvaluation:
    """한 planner prediction을 gold와 비교하고 redacted 결과만 반환한다."""
    errors = _prediction_shape_errors(
        prediction,
        history_ids=frozenset(message.id for message in fixture.history),
    )
    if errors:
        return CaseEvaluation(
            case_id=fixture.id,
            relation=fixture.gold.context_relation,
            critical=fixture.critical,
            repeat_index=repeat_index,
            schema_valid=False,
            checks={"schema_semantic_valid": False},
            selected_turn_precision=0.0,
            selected_turn_recall=0.0,
            latency_ms=float(latency_ms),
            input_tokens=max(0, int(input_tokens)),
            output_tokens=max(0, int(output_tokens)),
            context_reduction_rate=None,
            error_codes=errors,
        )

    context = prediction["context"]
    clarification = prediction["clarification"]
    fact_check = prediction["fact_check"]
    execution = prediction["execution"]
    predicted_ids = tuple(context["selected_turn_ids"])
    precision, recall = _selected_metrics(
        predicted_ids,
        fixture.gold.selected_turn_ids,
    )
    checks: dict[str, bool] = {
        "schema_semantic_valid": True,
        "context_relation": (
            context.get("relation") == fixture.gold.context_relation
        ),
        "context_selection": precision == 1.0 and recall == 1.0,
        "clarification": (
            clarification.get("required")
            is fixture.gold.clarification_required
        ),
        "execution_mode": execution.get("mode") == fixture.gold.execution_mode,
        "fact_required": (
            fact_check.get("required") is fixture.gold.fact_required
        ),
    }
    if fixture.gold.context_relation == "topic_shift":
        checks["topic_shift_no_context"] = not predicted_ids

    predicted_asset = _asset_name(execution)
    if fixture.gold.acceptable_assets:
        checks["asset"] = predicted_asset in fixture.gold.acceptable_assets
    else:
        checks["asset"] = not predicted_asset

    if fixture.gold.domains:
        checks["fact_domain"] = fact_check.get("domain") in fixture.gold.domains

    standalone_question = context.get("standalone_question", "")
    checks["normalized_terms"] = all(
        _contains_any(standalone_question, alternatives)
        for alternatives in fixture.gold.normalized_terms
    )

    predicted_entities = fact_check.get("entities", [])
    if not isinstance(predicted_entities, list):
        predicted_entities = []
    entity_text = " ".join(str(item) for item in predicted_entities)
    checks["entities"] = all(
        _contains_any(
            entity_text,
            (entity, *fixture.gold.entity_aliases.get(entity, ())),
        )
        for entity in fixture.gold.entities
    )

    query = fact_check.get("search_query", "")
    checks["neutral_query"] = not any(
        _contains_any(query, (term,))
        for term in fixture.gold.forbidden_query_terms
    )
    return CaseEvaluation(
        case_id=fixture.id,
        relation=fixture.gold.context_relation,
        critical=fixture.critical,
        repeat_index=repeat_index,
        schema_valid=True,
        checks=checks,
        selected_turn_precision=precision,
        selected_turn_recall=recall,
        latency_ms=max(0.0, float(latency_ms)),
        input_tokens=max(0, int(input_tokens)),
        output_tokens=max(0, int(output_tokens)),
        context_reduction_rate=_context_reduction(fixture, predicted_ids),
        error_codes=(),
    )


def percentile(values: Sequence[int | float], quantile: float) -> float | None:
    """선형 보간으로 deterministic percentile을 계산한다."""
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be between 0 and 1")
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = (len(ordered) - 1) * quantile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return (
        ordered[lower] * (upper - index)
        + ordered[upper] * (index - lower)
    )


def _mean(values: Sequence[int | float]) -> float | None:
    """빈 collection이면 null, 아니면 arithmetic mean을 반환한다."""
    return statistics.mean(values) if values else None


def aggregate_results(
    results: Sequence[CaseEvaluation],
    *,
    variant: str,
    repeat: int,
    baseline: str,
    live: bool = False,
    catalog_metrics: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """반복 run을 baseline 종류와 무관한 v1 JSON report로 집계한다."""
    ordered_results = list(results)
    latencies = [result.latency_ms for result in ordered_results]
    context_reductions = [
        result.context_reduction_rate
        for result in ordered_results
        if result.context_reduction_rate is not None
    ]
    critical = [result for result in ordered_results if result.critical]
    relation_summary: dict[str, dict[str, float | int]] = {}
    for relation in sorted(_CONTEXT_RELATIONS):
        relation_rows = [
            result
            for result in ordered_results
            if result.relation == relation
        ]
        if relation_rows:
            relation_summary[relation] = {
                "runs": len(relation_rows),
                "pass_rate": sum(row.passed for row in relation_rows)
                / len(relation_rows),
                "selected_turn_precision": statistics.mean(
                    row.selected_turn_precision for row in relation_rows
                ),
                "selected_turn_recall": statistics.mean(
                    row.selected_turn_recall for row in relation_rows
                ),
            }
    failures = [
        {
            "case_id": result.case_id,
            "repeat_index": result.repeat_index,
            "failed_checks": sorted(
                name for name, passed in result.checks.items() if not passed
            ),
            "error_codes": list(result.error_codes),
        }
        for result in ordered_results
        if not result.passed
    ]
    summary: dict[str, Any] = {
        "runs": len(ordered_results),
        "unique_cases": len({result.case_id for result in ordered_results}),
        "schema_success_rate": (
            sum(result.schema_valid for result in ordered_results)
            / len(ordered_results)
            if ordered_results
            else 0.0
        ),
        "pass_rate": (
            sum(result.passed for result in ordered_results)
            / len(ordered_results)
            if ordered_results
            else 0.0
        ),
        "macro_score": _mean(
            [result.macro_score for result in ordered_results]
        )
        or 0.0,
        "critical_pass_rate": (
            sum(result.passed for result in critical) / len(critical)
            if critical
            else None
        ),
        "selected_turn_precision": _mean(
            [result.selected_turn_precision for result in ordered_results]
        ),
        "selected_turn_recall": _mean(
            [result.selected_turn_recall for result in ordered_results]
        ),
        "latency_ms": {
            "avg": _mean(latencies),
            "p50": percentile(latencies, 0.5),
            "p95": percentile(latencies, 0.95),
        },
        "tokens": {
            "input_total": sum(
                result.input_tokens for result in ordered_results
            ),
            "output_total": sum(
                result.output_tokens for result in ordered_results
            ),
            "input_avg": _mean(
                [result.input_tokens for result in ordered_results]
            ),
            "output_avg": _mean(
                [result.output_tokens for result in ordered_results]
            ),
        },
        "context_reduction_rate": _mean(context_reductions),
    }
    if catalog_metrics is not None:
        expected_keys = {"asset_count", "character_count", "estimated_tokens"}
        if set(catalog_metrics) != expected_keys or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in catalog_metrics.values()
        ):
            raise ValueError(
                "catalog_metrics must contain non-negative integer "
                "asset_count, character_count, and estimated_tokens"
            )
        # 숫자 세 필드만 허용해 description/path/secret이 evaluator report로
        # 우회 유입되지 않게 한다.
        summary["catalog_payload"] = {
            key: catalog_metrics[key] for key in sorted(expected_keys)
        }

    return {
        "schema_version": "turn-planner-eval.v1",
        "benchmark": {
            "baseline": baseline,
            "reasoning": variant,
            "repeat": repeat,
            "live": live,
        },
        "summary": summary,
        "relations": relation_summary,
        "cases": [result.to_report() for result in ordered_results],
        "failures": failures,
    }


def evaluate_fixture_replays(
    fixtures: Sequence[TurnPlannerFixture],
    *,
    repeat: int,
    variant: str,
    baseline: str = "unified",
    catalog_metrics: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """fixture에 저장된 prediction을 반복 평가해 오프라인 report를 만든다."""
    if repeat < 1:
        raise ValueError("repeat must be at least 1")
    results: list[CaseEvaluation] = []
    for repeat_index in range(1, repeat + 1):
        for fixture in fixtures:
            if not fixture.prediction:
                raise FixtureFormatError(
                    f"fixture {fixture.id} has no offline replay prediction"
                )
            results.append(
                score_prediction(
                    fixture,
                    fixture.prediction,
                    latency_ms=fixture.metrics.get("latency_ms", 0),
                    input_tokens=int(
                        fixture.metrics.get("input_tokens", 0)
                    ),
                    output_tokens=int(
                        fixture.metrics.get("output_tokens", 0)
                    ),
                    repeat_index=repeat_index,
                )
            )
    return aggregate_results(
        results,
        variant=variant,
        repeat=repeat,
        baseline=baseline,
        live=False,
        catalog_metrics=catalog_metrics,
    )
