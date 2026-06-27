from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.fact_answer import compose_fact_answer
from simpleclaw.agent.fact_types import EvidenceCoverage, EvidenceItem, EvidenceSlot, FactPlan
from simpleclaw.llm.models import LLMResponse


@pytest.mark.asyncio
async def test_compose_fact_answer_uses_evidence_only():
    send = AsyncMock(return_value=LLMResponse(text="근거 기반 답변", tool_calls=None))
    slot = EvidenceSlot(name="current_state", question="현재 상태?")
    slot.add_evidence(EvidenceItem(
        source_url="https://official.example",
        source_type="official",
        claim="현재 상태는 X",
        coverage=EvidenceCoverage.FINAL,
        confidence="high",
    ))
    plan = FactPlan(task_type="complex_fact", complexity_score=3, slots=[slot])

    result = await compose_fact_answer(send, "질문", plan)

    assert result == "근거 기반 답변"
    request = send.call_args.args[0]
    assert "Use only the verified evidence" in request.system_prompt
    assert "현재 상태는 X" in request.user_message
