from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.fact_types import EvidenceCoverage, EvidenceItem
from simpleclaw.agent.fact_workflow import ComplexFactWorkflow, ComplexFactWorkflowConfig
from simpleclaw.agent.response_router import classify_response_route


@pytest.mark.asyncio
async def test_workflow_reports_missing_slots_when_evidence_unavailable():
    retriever = AsyncMock()
    retriever.search_for_slot.return_value = []
    composer = AsyncMock(return_value="근거가 부족해 확정 답변할 수 없습니다.")
    workflow = ComplexFactWorkflow(
        retriever=retriever,
        compose_answer=composer,
        config=ComplexFactWorkflowConfig(max_iterations=1, max_sources_per_slot=2),
    )
    question = "한국이 아직 16강 갈 가능성 있어? 경우의 수 알려줘"

    result = await workflow.run(question, classify_response_route(question))

    assert result.success is False
    assert "근거가 부족" in result.text
    assert result.plan.missing_required_slots()


@pytest.mark.asyncio
async def test_workflow_composes_when_required_slots_filled():
    async def fake_search(slot_name: str, query: str):
        return [EvidenceItem(
            source_url="https://official.example",
            source_type="official",
            claim=f"{slot_name} evidence",
            coverage=EvidenceCoverage.FINAL,
            confidence="high",
        )]

    retriever = AsyncMock()
    retriever.search_for_slot.side_effect = fake_search
    composer = AsyncMock(return_value="검증된 근거 기준 답변입니다.")
    workflow = ComplexFactWorkflow(
        retriever=retriever,
        compose_answer=composer,
        config=ComplexFactWorkflowConfig(max_iterations=5, max_sources_per_slot=2),
    )
    question = "한국이 아직 16강 갈 가능성 있어? 경우의 수 알려줘"

    result = await workflow.run(question, classify_response_route(question))

    assert result.success is True
    assert result.text == "검증된 근거 기준 답변입니다."
    assert result.plan.required_slots_complete()


@pytest.mark.asyncio
async def test_workflow_emits_progress_events_for_slots():
    events = []

    async def progress(event):
        events.append((event.kind, event.name, event.status))

    async def fake_search(slot_name: str, query: str):
        return [EvidenceItem(
            source_url="https://official.example",
            source_type="official",
            claim=f"{slot_name} evidence",
            coverage=EvidenceCoverage.FINAL,
            confidence="high",
        )]

    retriever = AsyncMock()
    retriever.search_for_slot.side_effect = fake_search
    composer = AsyncMock(return_value="답변")
    workflow = ComplexFactWorkflow(
        retriever=retriever,
        compose_answer=composer,
        config=ComplexFactWorkflowConfig(max_iterations=5, max_sources_per_slot=2),
    )
    question = "한국이 아직 16강 갈 가능성 있어? 경우의 수 알려줘"

    await workflow.run(question, classify_response_route(question), on_progress=progress)

    assert ("complex_fact", "current_state", "start") in events
    assert any(event[2] == "complete" for event in events)
