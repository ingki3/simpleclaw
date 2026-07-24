"""Complex factual/scenario workflow controller.

This is not a replacement for ToolLoopRunner. It is a controller for questions
that require evidence-slot completeness before final answer composition.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from simpleclaw.agent.claim_verification import verify_answer_claims
from simpleclaw.agent.evidence_validation import validate_slot_evidence
from simpleclaw.agent.fact_plan import build_fact_plan
from simpleclaw.agent.fact_types import ComplexFactResult, EvidenceItem, FactPlan
from simpleclaw.agent.progress import (
    ProgressCallback,
    ProgressEvent,
    emit_progress_event,
)
from simpleclaw.agent.response_router import RouteDecision


@dataclass(frozen=True)
class ComplexFactWorkflowConfig:
    max_iterations: int = 4
    max_sources_per_slot: int = 3
    enable_claim_verifier: bool = True
    enable_progress_events: bool = True


class ComplexFactWorkflow:
    """Fill evidence slots, then compose a constrained answer."""

    def __init__(
        self,
        *,
        retriever: object,
        compose_answer: Callable[[str, FactPlan], Awaitable[str]],
        config: ComplexFactWorkflowConfig,
    ) -> None:
        self._retriever = retriever
        self._compose_answer = compose_answer
        self._config = config

    async def run(
        self,
        question: str,
        decision: RouteDecision,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> ComplexFactResult:
        plan = build_fact_plan(
            question,
            decision,
            max_iterations=self._config.max_iterations,
        )
        for iterations, slot in enumerate(list(plan.slots)):
            if iterations >= plan.max_iterations:
                break
            if self._config.enable_progress_events:
                await emit_progress_event(
                    on_progress,
                    ProgressEvent("complex_fact", slot.name, "start", {"question": slot.question}),
                )
            query = self._query_for_slot(question, slot.name, slot.question)
            candidates: list[EvidenceItem] = await self._retriever.search_for_slot(
                slot.name,
                query,
            )
            validated = validate_slot_evidence(slot, candidates)
            idx = plan.slots.index(slot)
            plan.slots[idx] = validated
            if self._config.enable_progress_events:
                await emit_progress_event(
                    on_progress,
                    ProgressEvent("complex_fact", slot.name, "complete", {"status": validated.status.value}),
                )

        missing = plan.missing_required_slots()
        text = await self._compose_answer(question, plan)
        if not text.strip():
            if missing:
                names = ", ".join(slot.name for slot in missing)
                text = f"근거가 부족해 확정 답변할 수 없습니다. 부족한 슬롯: {names}"
            else:
                text = "검증된 근거를 바탕으로 답변을 생성하지 못했습니다."

        limitations: list[str] = []
        success = not missing
        if missing:
            limitations.append(
                "missing_required_slots: " + ", ".join(slot.name for slot in missing)
            )

        if self._config.enable_claim_verifier:
            verification = verify_answer_claims(text, plan)
            if not verification.allow_final:
                success = False
                limitations.extend(verification.unsupported_reasons)
                text = (
                    "근거 슬롯이 충분히 채워지지 않아 확정 표현은 제한합니다.\n\n"
                    f"부족/검증 필요: {', '.join(verification.unsupported_reasons)}\n\n"
                    f"초안: {text}"
                )

        return ComplexFactResult(
            text=text,
            plan=plan,
            success=success,
            limitations=limitations,
        )

    @staticmethod
    def _query_for_slot(question: str, slot_name: str, slot_question: str) -> str:
        return f"{question} {slot_name} {slot_question} official latest"
