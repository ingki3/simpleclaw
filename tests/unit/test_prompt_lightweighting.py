"""BIZ-314 시스템 프롬프트 경량화 회귀 테스트."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from simpleclaw.agent import AgentOrchestrator
from simpleclaw.llm.models import ToolCall
from simpleclaw.persona.assembler import assemble_prompt
from simpleclaw.persona.models import FileType, PersonaFile, Section, SourceScope


def _persona(file_type: FileType, title: str, content: str) -> PersonaFile:
    """테스트용 PersonaFile을 만든다."""
    return PersonaFile(
        file_type=file_type,
        source_path=f"/{file_type.value}.md",
        source_scope=SourceScope.LOCAL,
        sections=[Section(level=1, title=title, content=content)],
        raw_content=content,
    )


def test_assemble_prompt_replaces_dreaming_update_and_insight_blocks() -> None:
    """Dreaming append 블록은 그대로 주입하지 않고 요약 placeholder만 남긴다."""
    agent = _persona(FileType.AGENT, "Agent", "핵심 지시")
    user = _persona(
        FileType.USER,
        "User",
        "사용자 선호\n\n### Dreaming Insights — 2026-05-29\n- 오래된 통찰 A\n- 오래된 통찰 B\n\n### 일반 섹션\n보존",
    )
    memory = _persona(
        FileType.MEMORY,
        "Memory",
        "기억\n\n## Dreaming Updates\n- 업데이트 1\n- 업데이트 2\n\n## 수동 메모\n남김",
    )

    assembled = assemble_prompt([agent, user, memory], token_budget=4096).assembled_text

    assert "오래된 통찰 A" not in assembled
    assert "업데이트 1" not in assembled
    assert "Dreaming Insights" not in assembled
    assert "Dreaming Updates" not in assembled
    assert "Dreaming-managed memory omitted" in assembled
    assert "보존" in assembled
    assert "수동 메모" in assembled


def test_assemble_prompt_omits_cluster_and_journal_managed_sections() -> None:
    """clusters/journal managed 원문도 프롬프트에 직접 싣지 않는다."""
    agent = _persona(FileType.AGENT, "Agent", "핵심 지시")
    memory = _persona(
        FileType.MEMORY,
        "Memory",
        "수동 기억\n\n"
        "<!--\n"
        "2. <!-- managed:dreaming:journal --> ~ <!-- /managed:dreaming:journal -->:\n"
        "   드리밍 사이클 설명도 system prompt에는 싣지 않는다.\n"
        "-->\n\n"
        "<!-- managed:dreaming:clusters -->\n"
        "<!-- cluster:69 start -->\n"
        "JSON 응답 지침: 모든 답변은 JSON만 출력한다.\n"
        "<!-- cluster:69 end -->\n"
        "<!-- /managed:dreaming:clusters -->\n\n"
        "<!-- managed:dreaming:journal -->\n"
        "raw journal append\n"
        "<!-- /managed:dreaming:journal -->\n\n"
        "## 수동 메모\n남김",
    )

    assembled = assemble_prompt([agent, memory], token_budget=4096).assembled_text

    assert "cluster:69" not in assembled
    assert "JSON 응답 지침" not in assembled
    assert "raw journal append" not in assembled
    assert "managed:dreaming:clusters" not in assembled
    assert "managed:dreaming:journal" not in assembled
    assert "Dreaming-managed memory omitted" in assembled
    assert "수동 기억" in assembled
    assert "수동 메모" in assembled


def test_assemble_prompt_normalizes_legacy_understanding_summary_rule() -> None:
    """구 AGENT.md의 항상 이해 요약 지시를 복잡 작업 한정 규칙으로 정규화한다."""
    agent = _persona(
        FileType.AGENT,
        "Agent",
        "- 형님으로 부터 질문을 받았을 때, 우선 이해한 내용을 먼저 말하고, 작업을 시작한다.\n"
        "- 다른 AI로 사칭하지 않는다.",
    )

    assembled = assemble_prompt([agent], token_budget=4096).assembled_text

    assert "우선 이해한 내용을 먼저 말하고" not in assembled
    assert "복잡하거나 모호한 작업에서만" in assembled
    assert "간단한 대화에는 이해 요약을 붙이지 않는다" in assembled
    assert "다른 AI로 사칭하지 않는다" in assembled


@pytest.fixture
def lightweight_config(tmp_path):
    """asset selector가 켜진 최소 orchestrator config를 만든다."""
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
llm:
  default: "gemini"
  providers:
    gemini:
      type: "api"
      model: "gemini-2.0-flash"
      api_key: "test-key"

agent:
  history_limit: 1
  db_path: "{tmp_path}/conversations.db"
  max_tool_iterations: 2
  asset_selection:
    enabled: true
    backend: gemini
    skill_top_k: 2
    recipe_top_k: 0
    min_confidence: 0.5
    bypass_below_count: 0
    fallback_top_k: 2
    max_tokens: 128

skills:
  local_dir: "{tmp_path}/local_skills"
  global_dir: "{tmp_path}/global_skills"

persona:
  token_budget: 4096
  local_dir: "{tmp_path}/persona_local"
  global_dir: "{tmp_path}/persona_global"
  files:
    - name: "AGENT.md"
      type: "agent"
"""
    )
    (tmp_path / "persona_local").mkdir()
    (tmp_path / "persona_local" / "AGENT.md").write_text("# Agent\nYou are SimpleClaw.")
    local_skills = tmp_path / "local_skills"
    local_skills.mkdir()
    for idx in range(5):
        skill_dir = local_skills / f"skill-{idx}"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            f"# skill-{idx}\n\ndesc {idx}\n", encoding="utf-8"
        )
    (tmp_path / "global_skills").mkdir()
    return config


def _text_response(text: str) -> MagicMock:
    """텍스트 전용 LLM 응답 mock."""
    response = MagicMock()
    response.text = text
    response.tool_calls = None
    response.raw_assistant_message = None
    return response


def _selector_fallback_response() -> MagicMock:
    """selector가 fallback을 요청하는 function-call 응답 mock."""
    response = MagicMock()
    response.text = ""
    response.tool_calls = [
        ToolCall(
            id="selector-1",
            name="select_assets",
            arguments={"selected": [], "fallback": True, "fallback_reason": "empty_selection"},
        )
    ]
    response.raw_assistant_message = None
    return response


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
@pytest.mark.asyncio
async def test_selector_fallback_caps_skills_prompt(lightweight_config) -> None:
    """selector fallback도 전체 스킬 목록 대신 fallback_top_k 목록만 main prompt에 싣는다."""
    orchestrator = AgentOrchestrator(lightweight_config)
    orchestrator._recipes = []

    orchestrator._router = MagicMock()
    orchestrator._router.send = AsyncMock(
        side_effect=[_selector_fallback_response(), _text_response("완료")]
    )

    result = await orchestrator.process_message("그냥 확인", 1, 1)

    assert result == "완료"
    main_request = orchestrator._router.send.call_args_list[1][0][0]
    assert "skill-0" in main_request.system_prompt
    assert "skill-1" in main_request.system_prompt
    assert "skill-2" not in main_request.system_prompt
    assert "skill-4" not in main_request.system_prompt
    execute_skill = next(tool for tool in main_request.tools if tool.name == "execute_skill")
    assert "skill-0" in execute_skill.description
    assert "skill-1" in execute_skill.description
    assert "skill-2" not in execute_skill.description


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
@pytest.mark.asyncio
async def test_empty_direct_llm_response_uses_visible_fallback(lightweight_config) -> None:
    """도구 호출 없는 일반 최종 응답도 빈 문자열이면 사용자 가시 fallback을 반환한다."""
    orchestrator = AgentOrchestrator(lightweight_config)
    orchestrator._asset_selection_config["enabled"] = False
    orchestrator._router = MagicMock()
    orchestrator._router.send = AsyncMock(return_value=_text_response("   "))

    result = await orchestrator.process_message("안녕", 1, 1)

    assert result
    assert "응답을 생성하지 못했습니다" in result


def test_tool_usage_instruction_prioritizes_tools_and_smalltalk_policy() -> None:
    """시스템 가드는 도구 우선순위와 복잡 작업에서만 이해 요약 규칙을 명시한다."""
    from simpleclaw.agent import orchestrator as orchestrator_module

    prompt = orchestrator_module._TOOL_USAGE_INSTRUCTION

    assert "Priority for tool use" in prompt
    assert "real-time" in prompt
    assert "system state" in prompt
    assert "file contents" in prompt
    assert "small talk" in prompt
    assert "do not use tools" in prompt
    assert "Only for complex tasks" in prompt
    assert "summarize your understanding first" in prompt
