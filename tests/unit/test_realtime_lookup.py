"""BIZ-359 실시간 evidence 스킬 라우팅 회귀 테스트.

Gemini provider는 모델이 직접 반환하지 않은 synthetic functionCall history를 거부한다.
따라서 실시간성 보강은 assistant tool_call 합성이 아니라 runtime skill evidence를
system context로 주입하는 방식이어야 한다.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
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
  # BIZ-426 — realtime lookup 가드는 keyword route fallback 경로를 검증하므로
  # LLM turn analysis 를 끈다.
  turn_analysis:
    enabled: false

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
    # 프로덕션 reload 경로와 동일하게 내부 evidence 스킬은 callable 목록에서 제외한다.
    orchestrator._skills_prompt = orchestrator._format_skills_for_prompt(
        orchestrator._exposable_skills()
    )
    return skill


def _register_normal_skill(orchestrator: AgentOrchestrator, tmp_path):
    """LLM callable 일반 스킬을 등록해 노출 대비군으로 둔다."""
    skill_dir = tmp_path / "local_skills" / "echo-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    script = skill_dir / "echo_skill.py"
    script.write_text("print('hi')\n")
    (skill_dir / "SKILL.md").write_text(
        """---
name: echo-skill
description: Echo helper for testing
---
# echo-skill

## When to use
echo

## Script

Target: `echo_skill.py`
""",
        encoding="utf-8",
    )
    skill = SkillDefinition(
        name="echo-skill",
        description="Echo helper for testing",
        trigger="echo",
        skill_dir=str(skill_dir),
        script_path=str(script),
        scope=SkillScope.LOCAL,
    )
    realtime = orchestrator._skills_by_name.get("realtime-lookup-skill")
    skills = [skill] + ([realtime] if realtime is not None else [])
    orchestrator._skills = skills
    orchestrator._skills_by_name = {s.name: s for s in skills}
    orchestrator._skills_prompt = orchestrator._format_skills_for_prompt(
        orchestrator._exposable_skills()
    )
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
        return_value=(
            '{"kind":"news","confidence":"medium",'
            '"facts":[{"type":"source_document","source":"Example",'
            '"url":"https://example.com","title":"AI 뉴스 근거"}]}'
        )
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
    # BIZ-383: timeline validation 사용 규칙이 evidence context에 포함된다.
    assert "timeline_validation" in request.system_prompt
    assert "stale_or_pre_event" in request.system_prompt
    assert not any(m.get("role") == "tool" for m in request.messages)
    assert not any(m.get("tool_calls") for m in request.messages)


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evidence",
    [
        '{"kind":"sports","confidence":"low","facts":[{"type":"sports_score"}]}',
        '{"kind":"sports","confidence":"medium","facts":[]}',
    ],
)
async def test_low_or_empty_realtime_evidence_blocks_unsupported_score(
    config_file,
    tmp_path,
    evidence,
):
    """low/empty evidence는 2:1 승리·LIVE 같은 exact sports 문구를 허용하지 않는다."""
    orchestrator = AgentOrchestrator(config_file)
    _register_realtime_skill(orchestrator, tmp_path)
    orchestrator._execute_skill = AsyncMock(return_value=evidence)
    orchestrator._router = MagicMock()
    orchestrator._router.send = AsyncMock(
        return_value=_text_response("롯데가 2:1로 승리했고 현재 LIVE입니다.")
    )

    result = await orchestrator.process_message("롯데 야구 어케 되었나?", 1, 1)

    assert "확인하지 못" in result
    assert "2:1" not in result
    assert "LIVE" not in result


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
@pytest.mark.asyncio
async def test_one_sports_score_fact_allows_only_its_exact_result(
    config_file,
    tmp_path,
):
    """완결된 sports_score fact는 exact score final 답변의 usable evidence다."""
    orchestrator = AgentOrchestrator(config_file)
    _register_realtime_skill(orchestrator, tmp_path)
    orchestrator._execute_skill = AsyncMock(
        return_value=json.dumps(
            {
                "kind": "sports",
                "confidence": "high",
                "facts": [
                    {
                        "type": "sports_score",
                        "league": "KBO",
                        "event_date": "2026-07-24",
                        "status": "final",
                        "away_team": "kt wiz",
                        "away_score": 5,
                        "home_team": "롯데 자이언츠",
                        "home_score": 4,
                        "winner": "kt wiz",
                        "source": "Naver Sports Game Card",
                        "source_url": "https://search.naver.com/dated-game",
                    }
                ],
            },
            ensure_ascii=False,
        )
    )
    orchestrator._router = MagicMock()
    orchestrator._router.send = AsyncMock(
        return_value=_text_response("KT가 롯데를 5:4로 이겼고 경기는 종료됐습니다.")
    )

    result = await orchestrator.process_message("롯데 야구 어케 되었나?", 1, 1)

    assert result == "KT가 롯데를 5:4로 이겼고 경기는 종료됐습니다."
    request = orchestrator._router.send.call_args_list[0][0][0]
    assert "same `type: sports_score` fact" in request.system_prompt
    assert "Never merge values across snippets" in request.system_prompt


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
@pytest.mark.asyncio
async def test_runtime_realtime_source_reuses_builtin_web_fetch_policy(
    config_file,
    monkeypatch,
):
    """production realtime source fetch는 SSRF/redirect/headless web-fetch handler를 탄다."""
    orchestrator = AgentOrchestrator(config_file)
    fixture = (
        Path(__file__).parents[1]
        / "fixtures"
        / "realtime_lookup"
        / "naver_kbo_final.html"
    ).read_text(encoding="utf-8")
    fetched: list[str] = []

    async def fake_handle_web_fetch(routing, *, headless_binary=None):
        fetched.append(routing["url"])
        return fixture

    from simpleclaw.agent import builtin_tools

    monkeypatch.setattr(builtin_tools, "handle_web_fetch", fake_handle_web_fetch)
    raw = json.dumps(
        {
            "query": "롯데 야구 어케 되었나?",
            "as_of_kst": "2026-07-24T22:18:43+09:00",
        },
        ensure_ascii=False,
    ).encode("utf-8")
    token = base64.urlsafe_b64encode(raw).decode("ascii")

    output = await orchestrator._execute_skill("realtime-lookup-skill", token)
    result = json.loads(output or "{}")

    assert len(fetched) == 1
    assert "2026" in fetched[0]
    assert "duckduckgo.com" not in fetched[0]
    assert result["facts"][0]["type"] == "sports_score"
    assert result["facts"][0]["away_score"] == 5
    assert result["facts"][0]["home_score"] == 4


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
@pytest.mark.asyncio
async def test_market_impact_question_triggers_realtime_lookup(
    config_file,
    tmp_path,
):
    """BIZ-394: 기업·시장 이벤트 영향 질문은 시간 cue 없이도 evidence 조회를 탄다.

    "OpenAI 상장 ... 증시 영향 조사" 류는 standard default 로 떨어지지 않고 실시간
    evidence 스킬을 거쳐 근거 블록이 주입돼야 한다(구조적 도메인 cue).
    """
    orchestrator = AgentOrchestrator(config_file)
    _register_realtime_skill(orchestrator, tmp_path)
    orchestrator._execute_skill = AsyncMock(
        return_value=(
            '{"kind":"market","confidence":"medium",'
            '"facts":[{"type":"source_document","source":"Example",'
            '"url":"https://example.com","title":"증시 영향 근거"}]}'
        )
    )
    orchestrator._router = MagicMock()
    orchestrator._router.send = AsyncMock(return_value=_text_response("근거 기반 답변"))

    result = await orchestrator.process_message(
        "OpenAI 상장 연기가 증시에 끼치는 영향을 조사해줘", 1, 1
    )

    assert result == "근거 기반 답변"
    orchestrator._execute_skill.assert_awaited_once()
    skill_name, _payload = orchestrator._execute_skill.await_args.args
    assert skill_name == "realtime-lookup-skill"
    request = orchestrator._router.send.call_args_list[0][0][0]
    assert _REALTIME_LOOKUP_CONTEXT_HEADER in request.system_prompt
    assert "증시 영향 근거" in request.system_prompt


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
async def test_internal_realtime_skill_not_exposed_as_llm_callable(
    config_file,
    tmp_path,
):
    """내부 realtime skill evidence는 주입되지만 LLM callable 목록엔 노출되지 않는다."""
    orchestrator = AgentOrchestrator(config_file)
    _register_realtime_skill(orchestrator, tmp_path)
    normal = _register_normal_skill(orchestrator, tmp_path)
    orchestrator._execute_skill = AsyncMock(
        return_value=(
            '{"kind":"news","confidence":"medium",'
            '"facts":[{"type":"source_document","source":"Example",'
            '"url":"https://example.com","title":"근거"}]}'
        )
    )
    orchestrator._router = MagicMock()
    orchestrator._router.send = AsyncMock(return_value=_text_response("답변"))

    # 내부 evidence 스킬은 by-name 매핑으로 여전히 직접 실행 가능해야 한다.
    assert orchestrator._resolve_skill_name("realtime-lookup-skill") is not None
    # 그러나 LLM callable 후보(_exposable_skills)에서는 제외된다.
    exposable = {s.name for s in orchestrator._exposable_skills()}
    assert "realtime-lookup-skill" not in exposable
    assert normal.name in exposable

    await orchestrator.process_message("오늘 AI 최신 뉴스 알려줘", 1, 1)

    request = orchestrator._router.send.call_args_list[0][0][0]
    # evidence 블록은 주입되지만 callable skill 목록엔 internal skill 이름이 없다.
    assert _REALTIME_LOOKUP_CONTEXT_HEADER in request.system_prompt
    assert "realtime-lookup-skill" not in request.system_prompt
    assert normal.name in request.system_prompt


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
