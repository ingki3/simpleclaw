"""Answer composition for verified complex fact plans."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from simpleclaw.agent.fact_types import FactPlan
from simpleclaw.llm.models import LLMRequest, LLMResponse


async def compose_fact_answer(
    send: Callable[[LLMRequest], Awaitable[LLMResponse]],
    question: str,
    plan: FactPlan,
) -> str:
    """Ask the configured LLM to compose an answer from verified evidence only."""

    payload = {
        "question": question,
        "task_type": plan.task_type,
        "answer_contract": plan.answer_contract,
        "requires_calculation": plan.requires_calculation,
        "slots": [
            {
                "name": slot.name,
                "status": slot.status.value,
                "required": slot.required,
                "limitations": slot.limitations,
                "evidence": [
                    {
                        "claim": item.claim,
                        "value": item.extracted_value,
                        "source_url": item.source_url,
                        "source_title": item.source_title,
                        "source_type": item.source_type,
                        "source_time": item.source_time,
                        "coverage": item.coverage.value,
                        "confidence": item.confidence,
                    }
                    for item in slot.evidence
                ],
            }
            for slot in plan.slots
        ],
    }
    request = LLMRequest(
        system_prompt=(
            "Use only the verified evidence provided by the fact workflow. "
            "Do not introduce unsupported facts, numbers, dates, winners, prices, or rules. "
            "If required slots are missing, say what is missing and provide a limited answer. "
            "Respond in Korean 존댓말."
        ),
        user_message=json.dumps(payload, ensure_ascii=False),
        max_tokens=2048,
    )
    response = await send(request)
    return (response.text or "").strip()
