"""Orchestrator multimodal attachment preservation tests."""

from types import MethodType

import pytest

from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.llm.models import MultimodalAttachment


@pytest.mark.asyncio
async def test_process_message_passes_attachments_to_tool_loop():
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    captured = {}

    def reload_dynamic_files(self):
        return None

    async def fake_tool_loop(self, text, **kwargs):
        captured["text"] = text
        captured["attachments"] = kwargs.get("attachments")
        return "done"

    def save_turn(self, user_text, assistant_text, *, channel=None):
        return (1, 2)

    async def capture_conversation_end(self, user_text, assistant_text, source_msg_ids):
        return None

    orchestrator._reload_dynamic_files = MethodType(reload_dynamic_files, orchestrator)
    orchestrator._tool_loop = MethodType(fake_tool_loop, orchestrator)
    orchestrator._save_turn = MethodType(save_turn, orchestrator)
    orchestrator._capture_conversation_end_opportunity = MethodType(
        capture_conversation_end, orchestrator
    )
    orchestrator._cron_scheduler = None
    orchestrator._recipes_dir = "/tmp/unused-recipes"
    orchestrator._pending_clarify = {}

    attachment = MultimodalAttachment(data=b"img", mime_type="image/jpeg")
    response = await orchestrator.process_message(
        "사진 봐줘", 123, 456, attachments=[attachment]
    )

    assert response == "done"
    assert captured["text"] == "사진 봐줘"
    assert captured["attachments"] == [attachment]
