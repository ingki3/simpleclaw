"""BIZ-311 — 운영 tool loop의 asset selector top-k 배선을 검증한다."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from simpleclaw.agent import AgentOrchestrator
from simpleclaw.llm.models import ToolCall
from simpleclaw.recipes.models import RecipeDefinition
from simpleclaw.skills.models import SkillDefinition


@pytest.fixture
def config_file(tmp_path):
    """selector를 켠 최소 config.yaml을 만든다."""
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
  history_limit: 0
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
recipes:
  dir: "{tmp_path}/recipes"
asset_selection:
  enabled: true
  backend: "gemini"
  skill_top_k: 1
  recipe_top_k: 1
  min_confidence: 0.5
  bypass_below_count: 1
  fallback_top_k: 2
  max_tokens: 256
""")
    for dirname in ("persona_local", "local_skills", "global_skills", "recipes"):
        (tmp_path / dirname).mkdir()
    (tmp_path / "persona_local" / "AGENT.md").write_text("# Agent\nYou are SimpleClaw.")
    return config


def _text_response(text: str):
    resp = MagicMock()
    resp.text = text
    resp.tool_calls = None
    resp.raw_assistant_message = None
    return resp


def _selector_response(selected: list[dict], *, fallback: bool = False):
    resp = MagicMock()
    resp.text = ""
    resp.tool_calls = [
        ToolCall(
            id="selector-call",
            name="select_assets",
            arguments={"selected": selected, "fallback": fallback},
        )
    ]
    resp.raw_assistant_message = None
    return resp


def _install_assets(orchestrator: AgentOrchestrator) -> None:
    """테스트용 스킬/레시피 캐시를 직접 주입한다."""
    skills = [
        SkillDefinition(name="news", description="Search recent news."),
        SkillDefinition(name="mail", description="Search email."),
        SkillDefinition(name="calendar", description="Check calendars."),
    ]
    recipes = [
        RecipeDefinition(name="daily-report", description="Send a daily report."),
        RecipeDefinition(name="stock-close", description="Market close briefing."),
    ]
    orchestrator._skills = skills
    orchestrator._skills_by_name = {skill.name: skill for skill in skills}
    orchestrator._skills_prompt = orchestrator._format_skills_for_prompt(skills)
    orchestrator._recipes = recipes


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
@pytest.mark.asyncio
async def test_selector_high_confidence_limits_prompt_and_tool_schema(config_file):
    """selector 성공 시 선택된 top-k 스킬만 prompt와 execute_skill schema에 들어간다."""
    orchestrator = AgentOrchestrator(config_file)
    _install_assets(orchestrator)
    orchestrator._router = MagicMock()
    orchestrator._router.send = AsyncMock(
        side_effect=[
            _selector_response([
                {"type": "skill", "name": "mail", "confidence": 0.92, "reason": "email"},
                {"type": "skill", "name": "news", "confidence": 0.80, "reason": "news"},
                {"type": "recipe", "name": "daily-report", "confidence": 0.70, "reason": "report"},
            ]),
            _text_response("메일을 확인하겠습니다."),
        ]
    )

    result = await orchestrator._tool_loop("안 읽은 메일을 요약해줘", isolated=True)

    assert result == "메일을 확인하겠습니다."
    assert orchestrator._router.send.call_count == 2
    main_request = orchestrator._router.send.call_args_list[1][0][0]
    execute_skill = next(tool for tool in main_request.tools if tool.name == "execute_skill")
    assert "mail" in execute_skill.description
    assert "news" not in execute_skill.description
    system_prompt = main_request.system_prompt
    assert "mail" in system_prompt
    assert "- **news**" not in system_prompt
    assert "daily-report" not in system_prompt


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
@pytest.mark.asyncio
async def test_selector_low_confidence_falls_back_to_capped_assets(config_file):
    """낮은 confidence는 전체 후보가 아니라 fallback_top_k 만큼만 주입한다."""
    orchestrator = AgentOrchestrator(config_file)
    _install_assets(orchestrator)
    orchestrator._router = MagicMock()
    orchestrator._router.send = AsyncMock(
        side_effect=[
            _selector_response([
                {"type": "skill", "name": "mail", "confidence": 0.2, "reason": "weak"},
            ]),
            _text_response("제한 후보로 처리했습니다."),
        ]
    )

    result = await orchestrator._tool_loop("이거 좀 정리해줘", isolated=True)

    assert result == "제한 후보로 처리했습니다."
    main_request = orchestrator._router.send.call_args_list[1][0][0]
    execute_skill = next(tool for tool in main_request.tools if tool.name == "execute_skill")
    assert "mail" in execute_skill.description
    assert "news" in execute_skill.description
    assert "calendar" not in execute_skill.description
    assert "calendar" not in main_request.system_prompt


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
@pytest.mark.asyncio
async def test_selector_exception_falls_back_to_capped_assets(config_file):
    """selector 호출 실패도 main prompt에는 fallback_top_k 후보만 주입한다."""
    orchestrator = AgentOrchestrator(config_file)
    _install_assets(orchestrator)
    orchestrator._router = MagicMock()
    orchestrator._router.send = AsyncMock(
        side_effect=[RuntimeError("selector down"), _text_response("fallback ok")]
    )

    result = await orchestrator._tool_loop("메일을 확인해줘", isolated=True)

    assert result == "fallback ok"
    main_request = orchestrator._router.send.call_args_list[1][0][0]
    execute_skill = next(tool for tool in main_request.tools if tool.name == "execute_skill")
    assert "mail" in execute_skill.description
    assert "news" in execute_skill.description
    assert "calendar" not in execute_skill.description
    assert "calendar" not in main_request.system_prompt


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
@pytest.mark.asyncio
async def test_selector_bypasses_when_candidate_count_under_threshold(config_file):
    """후보 수가 bypass_below_count 이하이면 selector 호출 자체를 생략한다."""
    orchestrator = AgentOrchestrator(config_file)
    skills = [SkillDefinition(name="only", description="Only skill.")]
    orchestrator._skills = skills
    orchestrator._skills_by_name = {"only": skills[0]}
    orchestrator._skills_prompt = orchestrator._format_skills_for_prompt(skills)
    orchestrator._recipes = []
    orchestrator._router = MagicMock()
    orchestrator._router.send = AsyncMock(return_value=_text_response("바로 처리했습니다."))

    result = await orchestrator._tool_loop("도와줘", isolated=True)

    assert result == "바로 처리했습니다."
    assert orchestrator._router.send.call_count == 1
