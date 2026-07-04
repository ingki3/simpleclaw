"""Fact plan construction for complex factual/scenario questions."""

from __future__ import annotations

from simpleclaw.agent.fact_types import EvidenceSlot, FactPlan
from simpleclaw.agent.response_router import RouteDecision


def build_fact_plan(
    question: str,
    decision: RouteDecision,
    *,
    max_iterations: int,
) -> FactPlan:
    """Build required evidence slots from a structural route decision."""

    slots: list[EvidenceSlot] = []

    if decision.needs_current_facts:
        slots.append(EvidenceSlot(
            name="current_state",
            question="What is the current/latest state relevant to the user's question?",
            required=True,
            freshness_required=True,
            preferred_source_type="official",
        ))

    if decision.needs_rules:
        slots.append(EvidenceSlot(
            name="decision_rules",
            question="What rule, format, threshold, policy, or tiebreaker governs the answer?",
            required=True,
            freshness_required=False,
            preferred_source_type="official",
        ))

    if decision.needs_remaining_variables:
        slots.append(EvidenceSlot(
            name="remaining_variables",
            question="What remaining events or variables can still change the answer?",
            required=True,
            freshness_required=True,
            preferred_source_type="official",
            depends_on=["current_state"],
        ))

    if decision.needs_comparison_or_conditions:
        slots.append(EvidenceSlot(
            name="comparison_set",
            question="Which entities, groups, conditions, or comparison table must be checked?",
            required=decision.needs_remaining_variables,
            freshness_required=decision.needs_current_facts,
            preferred_source_type="official",
        ))
        if not decision.needs_remaining_variables:
            slots.append(EvidenceSlot(
                name="subject_conditions",
                question="What user-specific or case-specific conditions must be matched against the rule?",
                required=True,
                freshness_required=False,
            ))

    if decision.needs_calculation:
        slots.append(EvidenceSlot(
            name="calculation_inputs",
            question="What structured inputs are required for calculation, enumeration, ranking, or eligibility judgment?",
            required=True,
            freshness_required=decision.needs_current_facts,
        ))

    if not slots:
        slots.append(EvidenceSlot(
            name="primary_fact",
            question="What primary fact is needed to answer the question?",
            required=True,
            freshness_required=decision.needs_current_facts,
        ))

    return FactPlan(
        task_type="scenario_analysis" if decision.needs_remaining_variables else "complex_fact",
        complexity_score=decision.complexity_score,
        slots=slots,
        requires_calculation=decision.needs_calculation,
        max_iterations=max_iterations,
        answer_contract=(
            "Answer only from filled evidence slots. Separate conclusion, verified facts, "
            "scenario/condition analysis, limitations, and source/as-of notes."
        ),
    )
