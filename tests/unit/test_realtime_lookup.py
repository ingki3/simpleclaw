"""BIZ-359 실시간 evidence 스킬 라우팅 회귀 테스트.

Gemini provider는 모델이 직접 반환하지 않은 synthetic functionCall history를 거부한다.
따라서 실시간성 보강은 assistant tool_call 합성이 아니라 runtime skill evidence를
system context로 주입하는 방식이어야 한다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from simpleclaw.agent import AgentOrchestrator
from simpleclaw.agent.orchestrator import _REALTIME_LOOKUP_CONTEXT_HEADER
from simpleclaw.skills.models import SkillDefinition, SkillScope


@pytest.fixture
def config_file(tmp_path):
    """테스트용 최소 SimpleClaw 설정 파일을 만든다."""
    config = tmp_path / "config.yaml"
    config.write_text(f"""
llm:
  default: "gemini"
  providers:
    gemini:
      type: "api"
      model: "gemini-2.0-flash"
      api_key: "test-key"

agent:
  history_limit: 5
  db_path: "{tmp_path}/conversations.db"
  max_tool_iterations: 3

security:
  command_guard:
    enabled: true
    allowlist: []
  env_passthrough: []

skills:
  local_dir: "{tmp_path}/local_skills"
  global_dir: "{tmp_path}/global_skills"
  execution_timeout: 30

persona:
  token_budget: 4096
  local_dir: "{tmp_path}/persona_local"
  global_dir: "{tmp_path}/persona_global"
  files:
    - name: "AGENT.md"
      type: "agent"
""")
    persona_dir = tmp_path / "persona_local"
    persona_dir.mkdir()
    (persona_dir / "AGENT.md").write_text("# Agent\nYou are SimpleClaw.")
    (tmp_path / "local_skills").mkdir()
    (tmp_path / "global_skills").mkdir()
    return config


def _text_response(text: str) -> MagicMock:
    """텍스트 final response mock을 만든다."""
    resp = MagicMock()
    resp.text = text
    resp.tool_calls = None
    resp.backend_name = "gemini"
    resp.raw_assistant_message = None
    return resp


def _register_realtime_skill(orchestrator: AgentOrchestrator, tmp_path):
    """오케스트레이터가 hot-reload로 발견할 fake realtime skill 파일을 만든다."""
    skill_dir = tmp_path / "local_skills" / "realtime-lookup-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    script = skill_dir / "realtime_lookup_skill.py"
    script.write_text("print('{}')\n")
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        """---
name: realtime-lookup-skill
description: Produce realtime evidence
---
# realtime-lookup-skill

## When to use
오늘, 현재, 최신, 뉴스, 날씨, 주가

## Script

Target: `realtime_lookup_skill.py`
""",
        encoding="utf-8",
    )
    skill = SkillDefinition(
        name="realtime-lookup-skill",
        description="Produce realtime evidence",
        trigger="오늘, 현재, 최신, 뉴스, 날씨, 주가",
        skill_dir=str(skill_dir),
        script_path=str(script),
        scope=SkillScope.LOCAL,
    )
    orchestrator._skills = [skill]
    orchestrator._skills_by_name = {skill.name: skill}
    orchestrator._skills_prompt = orchestrator._format_skills_for_prompt([skill])
    return skill


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
@pytest.mark.asyncio
async def test_live_fact_uses_realtime_lookup_context_without_synthetic_tool_call(
    config_file,
    tmp_path,
):
    """실시간 질문은 synthetic web_fetch tool_call 없이 skill evidence를 주입한다."""
    orchestrator = AgentOrchestrator(config_file)
    _register_realtime_skill(orchestrator, tmp_path)
    orchestrator._execute_skill = AsyncMock(
        return_value='{"kind":"news","facts":[{"claim":"AI 뉴스 근거"}]}'
    )
    orchestrator._router = MagicMock()
    orchestrator._router.send = AsyncMock(return_value=_text_response("근거 기반 답변"))

    result = await orchestrator.process_message("오늘 AI 최신 뉴스 알려줘", 1, 1)

    assert result == "근거 기반 답변"
    orchestrator._execute_skill.assert_awaited_once()
    await_args = orchestrator._execute_skill.await_args
    assert await_args is not None
    skill_name, payload = await_args.args
    assert skill_name == "realtime-lookup-skill"
    assert isinstance(payload, str) and " " not in payload

    request = orchestrator._router.send.call_args_list[0][0][0]
    assert _REALTIME_LOOKUP_CONTEXT_HEADER in request.system_prompt
    assert "AI 뉴스 근거" in request.system_prompt
    assert not any(m.get("role") == "tool" for m in request.messages)
    assert not any(m.get("tool_calls") for m in request.messages)


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
@pytest.mark.asyncio
async def test_live_fact_without_realtime_skill_does_not_force_web_fetch(
    config_file,
):
    """스킬이 없더라도 Gemini-breaking synthetic web_fetch는 만들지 않는다."""
    orchestrator = AgentOrchestrator(config_file)
    orchestrator._execute_skill = AsyncMock()
    orchestrator._router = MagicMock()
    orchestrator._router.send = AsyncMock(return_value=_text_response("직접 답변"))

    result = await orchestrator.process_message("오늘 AI 최신 뉴스 알려줘", 1, 1)

    assert "확인하지 못" in result
    assert "직접 답변" not in result
    orchestrator._execute_skill.assert_not_called()
    assert orchestrator._router.send.call_count == 1
    request = orchestrator._router.send.call_args_list[0][0][0]
    assert not any(m.get("role") == "tool" for m in request.messages)
    assert not any(m.get("tool_calls") for m in request.messages)


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
@pytest.mark.asyncio
async def test_non_live_question_does_not_invoke_realtime_lookup(
    config_file,
    tmp_path,
):
    """비실시간 설명 질문은 realtime lookup skill을 선실행하지 않는다."""
    orchestrator = AgentOrchestrator(config_file)
    _register_realtime_skill(orchestrator, tmp_path)
    orchestrator._execute_skill = AsyncMock()
    orchestrator._router = MagicMock()
    orchestrator._router.send = AsyncMock(return_value=_text_response("리스트 설명"))

    result = await orchestrator.process_message("파이썬 리스트가 뭐야?", 1, 1)

    assert result == "리스트 설명"
    orchestrator._execute_skill.assert_not_called()
    request = orchestrator._router.send.call_args_list[0][0][0]
    assert _REALTIME_LOOKUP_CONTEXT_HEADER not in request.system_prompt
