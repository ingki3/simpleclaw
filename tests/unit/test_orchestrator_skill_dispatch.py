"""Orchestrator 의 BIZ-166 skill dispatch 정규화 테스트.

검증 범위:
- bare skill 이름으로 들어온 명령이 ``<venv>/bin/python <script_path> ...`` 로
  자동 치환되는지 (등록된 .py skill 한정).
- 등록되지 않은 첫 토큰(예: ``agent-browser open ... && wait ...``) 은 통과.
- ``python script.py`` 의 기존 인터프리터 치환 동작도 보존.
- ``_format_skills_for_prompt`` 출력에 정확한 호출 형식이 들어 있는지.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from simpleclaw.agent import AgentOrchestrator
from simpleclaw.skills.models import SkillDefinition, SkillScope


@pytest.fixture
def config_file(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"""
llm:
  default: "gemini"
  providers:
    gemini:
      type: "api"
      model: "gemini-2.0-flash"
      api_key: "test-key"

agent:
  history_limit: 3
  db_path: "{tmp_path}/conversations.db"

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

memory:
  rag:
    enabled: false
""")
    persona_dir = tmp_path / "persona_local"
    persona_dir.mkdir()
    (persona_dir / "AGENT.md").write_text("# Agent\nYou are SimpleClaw.")
    (tmp_path / "local_skills").mkdir()
    (tmp_path / "global_skills").mkdir()
    return cfg


def _make_python_skill(tmp_path, name: str) -> SkillDefinition:
    """script.py + 인근 venv/bin/python (실제 파일) 을 만들어 SkillDefinition 반환."""
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    script = skill_dir / "script.py"
    script.write_text("import sys; print('ok', *sys.argv[1:])")
    venv_python = skill_dir / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\nexec python3 \"$@\"\n")
    venv_python.chmod(0o755)
    return SkillDefinition(
        name=name,
        description=f"test skill {name}",
        script_path=str(script),
        skill_dir=str(skill_dir),
        scope=SkillScope.LOCAL,
    )


def test_bare_skill_name_rewritten_to_venv_python(config_file, tmp_path):
    """`news-search-skill "foo"` → `<venv>/bin/python <script> "foo"` 로 치환."""
    orch = AgentOrchestrator(config_file)
    skill = _make_python_skill(tmp_path, "news-search-skill")
    orch._skills_by_name = {skill.name: skill}

    result = orch._normalize_skill_command('news-search-skill "foo bar"')

    expected_python = Path(skill.skill_dir) / "venv" / "bin" / "python"
    expected_script = Path(skill.script_path)
    assert str(expected_python) in result, (
        f"venv python 경로가 명령에 들어가야 함: {result}"
    )
    assert str(expected_script) in result
    assert '"foo bar"' in result


def test_bare_skill_no_args_handled(config_file, tmp_path):
    """args 없이 skill 이름만 와도 venv-direct 로 치환된다."""
    orch = AgentOrchestrator(config_file)
    skill = _make_python_skill(tmp_path, "news-search-skill")
    orch._skills_by_name = {skill.name: skill}

    result = orch._normalize_skill_command("news-search-skill")

    expected_python = Path(skill.skill_dir) / "venv" / "bin" / "python"
    assert str(expected_python) in result
    assert str(Path(skill.script_path)) in result


def test_unregistered_first_token_pass_through(config_file, tmp_path):
    """첫 토큰이 등록 skill 이름이 아니면 변경하지 않는다 (composite 명령 보호)."""
    orch = AgentOrchestrator(config_file)
    orch._skills_by_name = {}

    composite = 'agent-browser open "https://a" && agent-browser wait --load load'
    result = orch._normalize_skill_command(composite)

    assert result == composite, (
        "등록되지 않은 명령은 그대로 통과해야 한다 (agent-browser 류 composite)"
    )


def test_uvx_invocation_pass_through_when_skill_unregistered(config_file):
    """`uvx <name>` 처럼 첫 토큰이 등록 skill 아니면 그대로 통과 — 셸이 처리."""
    orch = AgentOrchestrator(config_file)
    orch._skills_by_name = {}

    cmd = "uvx news-search-skill 'foo'"
    assert orch._normalize_skill_command(cmd) == cmd


def test_uvx_invocation_rewritten_when_skill_registered(config_file, tmp_path):
    """BIZ-166 follow-up: `uvx <registered-skill>` → venv-direct 치환.

    gemini-3-flash-preview 가 시스템 프롬프트의 "uvx 금지" 안내를 무시하고 첫
    시도를 ``uvx news-search-skill "..."`` 로 시도하는 패턴(2026-05-12 다발)을
    런타임에서 강제로 봉합한다.
    """
    orch = AgentOrchestrator(config_file)
    skill = _make_python_skill(tmp_path, "news-search-skill")
    orch._skills_by_name = {skill.name: skill}

    result = orch._normalize_skill_command('uvx news-search-skill "foo bar"')

    expected_python = Path(skill.skill_dir) / "venv" / "bin" / "python"
    expected_script = Path(skill.script_path)
    assert str(expected_python) in result, (
        f"uvx prefix 가 제거되고 venv python 경로가 들어가야 함: {result}"
    )
    assert str(expected_script) in result
    assert '"foo bar"' in result
    # 첫 토큰이 uvx 가 아니어야 함 (셸이 venv python 을 직접 실행)
    assert not result.lstrip().startswith("uvx ")


def test_pipx_run_invocation_rewritten_when_skill_registered(
    config_file, tmp_path,
):
    """`pipx run <registered-skill> ...` 도 동일 규칙으로 venv-direct 로 치환."""
    orch = AgentOrchestrator(config_file)
    skill = _make_python_skill(tmp_path, "news-search-skill")
    orch._skills_by_name = {skill.name: skill}

    result = orch._normalize_skill_command(
        'pipx run news-search-skill --query "hello"'
    )

    expected_python = Path(skill.skill_dir) / "venv" / "bin" / "python"
    assert str(expected_python) in result
    assert str(Path(skill.script_path)) in result
    assert '"hello"' in result
    assert not result.lstrip().startswith("pipx ")


def test_uvx_with_unregistered_inner_skill_pass_through(config_file, tmp_path):
    """`uvx <unknown> ...` 은 진짜 PyPI 호출일 수 있으므로 통과해야 한다."""
    orch = AgentOrchestrator(config_file)
    # news-search-skill 만 등록. uvx 뒤의 토큰이 등록 skill 이 아니면 통과.
    skill = _make_python_skill(tmp_path, "news-search-skill")
    orch._skills_by_name = {skill.name: skill}

    cmd = "uvx ruff check src/"
    assert orch._normalize_skill_command(cmd) == cmd


def test_format_skills_for_prompt_bans_uvx(config_file, tmp_path):
    """`_format_skills_for_prompt` 출력에 uvx/pipx 금지 안내가 포함된다."""
    orch = AgentOrchestrator(config_file)
    skill = _make_python_skill(tmp_path, "news-search-skill")

    formatted = orch._format_skills_for_prompt([skill])

    assert "uvx" in formatted
    assert "pipx" in formatted


def test_tool_usage_instruction_bans_uvx():
    """`_TOOL_USAGE_INSTRUCTION` 에 uvx/pipx 금지 안내가 명시되어 있다.

    BIZ-166: 시스템 프롬프트가 모델의 첫 시도를 venv-direct 형태로 유도해야 한다.
    """
    from simpleclaw.agent.orchestrator import _TOOL_USAGE_INSTRUCTION

    assert "uvx" in _TOOL_USAGE_INSTRUCTION
    assert "pipx" in _TOOL_USAGE_INSTRUCTION
    assert "execute_skill" in _TOOL_USAGE_INSTRUCTION


def test_tool_usage_instruction_prefers_web_fetch_over_agent_browser():
    """BIZ-167 — 본문 읽기는 web_fetch 우선, agent-browser networkidle 함정 경고."""
    from simpleclaw.agent.orchestrator import _TOOL_USAGE_INSTRUCTION

    # web_fetch 가 본문 회수의 디폴트라는 안내가 박혀 있어야 한다.
    assert "web_fetch" in _TOOL_USAGE_INSTRUCTION
    # composite agent-browser 명령 첫 시도 패턴을 명시적으로 차단한다.
    assert "agent-browser" in _TOOL_USAGE_INSTRUCTION
    # networkidle 함정 + 권장 wait strategy 가 동시에 보여야 한다.
    assert "networkidle" in _TOOL_USAGE_INSTRUCTION
    assert "wait --load load" in _TOOL_USAGE_INSTRUCTION


def test_python_script_interpreter_substitution_preserved(config_file, tmp_path):
    """`python script.py` 의 인터프리터를 venv python 으로 치환하는 기존 동작 보존."""
    orch = AgentOrchestrator(config_file)
    orch._skills_by_name = {}

    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    script = skill_dir / "demo.py"
    script.write_text("print(1)")
    venv_python = skill_dir / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\nexec python3 \"$@\"\n")
    venv_python.chmod(0o755)

    result = orch._normalize_skill_command(f"python {script} --foo")

    assert str(venv_python) in result
    assert "--foo" in result


def test_format_skills_for_prompt_includes_invocation(config_file, tmp_path):
    """`_format_skills_for_prompt` 가 정확한 execute_skill 호출 형식을 명시한다."""
    orch = AgentOrchestrator(config_file)
    skill = _make_python_skill(tmp_path, "news-search-skill")

    formatted = orch._format_skills_for_prompt([skill])

    assert "## Available Skills" in formatted
    assert "news-search-skill" in formatted
    assert "execute_skill" in formatted
    assert 'skill_name="news-search-skill"' in formatted
    assert "Do NOT compose your own bare command" in formatted


def test_format_skills_falls_back_to_skill_docs_for_non_python(
    config_file, tmp_path,
):
    """script_path 가 .py 가 아니거나 비어 있으면 skill_docs 안내로 폴백."""
    orch = AgentOrchestrator(config_file)
    skill = SkillDefinition(
        name="agent-browser",
        description="composite CLI",
        script_path="",  # 빈 script_path
        skill_dir=str(tmp_path / "agent-browser"),
        scope=SkillScope.LOCAL,
    )

    formatted = orch._format_skills_for_prompt([skill])

    assert "agent-browser" in formatted
    assert 'skill_docs("agent-browser")' in formatted


# ----------------------------------------------------------------------
# BIZ-187 — agent-browser composite skill-command 타임아웃 화이트리스트
# ----------------------------------------------------------------------


def test_agent_browser_command_extends_skill_timeout(config_file):
    """`agent-browser` 가 들어간 명령은 확장 타임아웃이 적용된다."""
    orch = AgentOrchestrator(config_file)
    # 기본값: skill_timeout=60, agent_browser_timeout=180
    orch._skill_timeout = 60
    orch._agent_browser_timeout = 180

    composite = (
        'agent-browser open "https://wikidocs.net/12345" && '
        'agent-browser wait --load load && '
        'agent-browser get text body'
    )
    assert orch._resolve_command_timeout(composite) == 180


def test_single_agent_browser_command_uses_extended_timeout(config_file):
    """composite 가 아닌 단일 ``agent-browser`` 호출에도 확장 타임아웃 적용."""
    orch = AgentOrchestrator(config_file)
    orch._skill_timeout = 60
    orch._agent_browser_timeout = 180

    assert (
        orch._resolve_command_timeout("agent-browser get text body") == 180
    )


def test_non_agent_browser_command_uses_default_skill_timeout(config_file):
    """``agent-browser`` 없는 명령은 기존 ``_skill_timeout`` 유지."""
    orch = AgentOrchestrator(config_file)
    orch._skill_timeout = 60
    orch._agent_browser_timeout = 180

    assert orch._resolve_command_timeout("python script.py --foo") == 60
    assert orch._resolve_command_timeout('curl "https://example.com"') == 60


def test_agent_browser_timeout_disabled_when_below_skill_timeout(config_file):
    """``agent_browser_command_timeout`` 이 ``_skill_timeout`` 이하면 확장 비활성.

    운영자가 ``agent_browser_command_timeout`` 을 낮추는 식으로 사실상 비활성화
    하려는 경우 (또는 ``skills.execution_timeout`` 을 180+ 로 올려둔 경우) 에는
    화이트리스트 분기가 동작하지 않고 기본 타임아웃을 그대로 쓰도록 한다.
    """
    orch = AgentOrchestrator(config_file)
    orch._skill_timeout = 60
    orch._agent_browser_timeout = 60  # 동일 → 분기 skip

    assert (
        orch._resolve_command_timeout("agent-browser get text body") == 60
    )


def test_tool_usage_instruction_warns_against_chained_agent_browser():
    """BIZ-187 — composite chain 분해 가이드가 시스템 프롬프트에 박혀 있다."""
    from simpleclaw.agent.orchestrator import _TOOL_USAGE_INSTRUCTION

    # ``open → wait → text`` 를 각자 별 turn 으로 쪼개라는 안내 키워드.
    assert "separate turns" in _TOOL_USAGE_INSTRUCTION
    # composite chain 의 위험성을 명시적으로 박아 둠.
    assert "&&" in _TOOL_USAGE_INSTRUCTION


# ----------------------------------------------------------------------
# BIZ-190 — composite agent-browser chain 차단 + per-turn 호출 cap
# ----------------------------------------------------------------------


def test_is_composite_agent_browser_chain_detects_double_ampersand():
    """``agent-browser open ... && wait ... && evaluate ...`` chain 식별."""
    from simpleclaw.agent.orchestrator import AgentOrchestrator

    composite = (
        'agent-browser open "https://wikidocs.net/3753" && '
        'agent-browser wait --load load && '
        'agent-browser evaluate "document.title"'
    )
    assert AgentOrchestrator._is_composite_agent_browser_chain(composite) is True


def test_is_composite_agent_browser_chain_detects_semicolon():
    """``;`` 로 묶인 chain 도 차단 대상."""
    from simpleclaw.agent.orchestrator import AgentOrchestrator

    cmd = "agent-browser open https://x ; agent-browser wait --load load"
    assert AgentOrchestrator._is_composite_agent_browser_chain(cmd) is True


def test_is_composite_agent_browser_chain_single_call_allowed():
    """단일 ``agent-browser`` 호출은 차단하지 않음 — composite 만 거른다."""
    from simpleclaw.agent.orchestrator import AgentOrchestrator

    assert (
        AgentOrchestrator._is_composite_agent_browser_chain(
            "agent-browser open https://example.com"
        )
        is False
    )
    assert (
        AgentOrchestrator._is_composite_agent_browser_chain(
            "agent-browser get text body"
        )
        is False
    )


def test_is_composite_agent_browser_chain_non_agent_browser_unaffected():
    """``agent-browser`` 가 들어 있지 않은 명령은 ``&&`` 가 있어도 통과."""
    from simpleclaw.agent.orchestrator import AgentOrchestrator

    cmd = 'curl https://a && curl https://b'
    assert AgentOrchestrator._is_composite_agent_browser_chain(cmd) is False


@pytest.mark.asyncio
async def test_execute_command_blocks_composite_agent_browser_chain(
    config_file, caplog,
):
    """``_execute_command`` 가 composite chain 을 subprocess 전에 차단한다.

    BIZ-190: BIZ-187 의 180s 화이트리스트 + 가드 텍스트만으로는 작은 모델이
    같은 chain 을 재시도하는 패턴이 잡히지 않아, 실행 직전에 합성 응답으로
    돌려준다. 같은 turn 안에서 LLM 이 정정하도록.
    """
    import logging

    from simpleclaw.agent import AgentOrchestrator
    from simpleclaw.agent.orchestrator import (
        _AGENT_BROWSER_COMPOSITE_BLOCKED_MESSAGE,
    )

    orch = AgentOrchestrator(config_file)
    composite = (
        'agent-browser open "https://wikidocs.net/3753" && '
        'agent-browser wait --load load && '
        'agent-browser evaluate "document.title"'
    )

    with caplog.at_level(
        logging.WARNING, logger="simpleclaw.agent.orchestrator"
    ):
        result = await orch._execute_command("agent-browser", composite)

    assert result == _AGENT_BROWSER_COMPOSITE_BLOCKED_MESSAGE, (
        "composite chain 은 명시적 안내 메시지로 즉시 응답해야 한다"
    )
    assert "BIZ-190: composite agent-browser chain blocked" in caplog.text


@pytest.mark.asyncio
async def test_execute_command_single_agent_browser_call_not_blocked(
    config_file, monkeypatch,
):
    """단일 ``agent-browser`` 호출은 정상적으로 subprocess 로 흐른다."""
    from simpleclaw.agent import AgentOrchestrator

    orch = AgentOrchestrator(config_file)

    # subprocess 진입 자체를 가짜 — async create_subprocess_shell 를 mock 한다.
    class _FakeProc:
        def __init__(self):
            self.returncode = 0

        async def communicate(self):
            return b"ok", b""

    async def fake_create(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(
        "asyncio.create_subprocess_shell", fake_create,
    )

    result = await orch._execute_command(
        "agent-browser", "agent-browser get text body",
    )
    # composite block 메시지가 절대 흘러나오지 않아야 한다 (회귀 가드)
    assert "composite `agent-browser` chains are blocked" not in result
    assert result == "ok"


def test_call_invokes_agent_browser_detects_via_skill_name():
    """``execute_skill(skill_name="agent-browser", ...)`` 는 카운트 대상."""
    from simpleclaw.agent.orchestrator import AgentOrchestrator
    from simpleclaw.llm.models import ToolCall

    tc = ToolCall(
        id="t1",
        name="execute_skill",
        arguments={"skill_name": "agent-browser", "args": "open https://x"},
    )
    assert AgentOrchestrator._call_invokes_agent_browser(tc) is True


def test_call_invokes_agent_browser_detects_via_command():
    """``execute_skill`` 의 ``command`` 가 agent-browser 호출이면 카운트."""
    from simpleclaw.agent.orchestrator import AgentOrchestrator
    from simpleclaw.llm.models import ToolCall

    tc = ToolCall(
        id="t1",
        name="execute_skill",
        arguments={
            "skill_name": "other",
            "command": "agent-browser open https://x",
        },
    )
    assert AgentOrchestrator._call_invokes_agent_browser(tc) is True


def test_call_invokes_agent_browser_detects_via_cli():
    """``cli`` 도구로 우회 호출해도 카운트 대상."""
    from simpleclaw.agent.orchestrator import AgentOrchestrator
    from simpleclaw.llm.models import ToolCall

    tc = ToolCall(
        id="t1",
        name="cli",
        arguments={"command": "agent-browser get text body"},
    )
    assert AgentOrchestrator._call_invokes_agent_browser(tc) is True


def test_call_invokes_agent_browser_ignores_unrelated_calls():
    """다른 도구/명령은 카운트되지 않는다."""
    from simpleclaw.agent.orchestrator import AgentOrchestrator
    from simpleclaw.llm.models import ToolCall

    cases = [
        ToolCall(id="t1", name="web_fetch", arguments={"url": "https://x"}),
        ToolCall(
            id="t2", name="execute_skill",
            arguments={"skill_name": "summarize", "args": "https://x"},
        ),
        ToolCall(id="t3", name="cli", arguments={"command": "ls -la"}),
        ToolCall(id="t4", name="skill_docs", arguments={"name": "agent-browser"}),
    ]
    for tc in cases:
        assert AgentOrchestrator._call_invokes_agent_browser(tc) is False, (
            f"오탐 — {tc.name}({tc.arguments}) 는 agent-browser 호출이 아님"
        )


def test_tool_usage_instruction_includes_fetch_blocked_guidance():
    """BIZ-190 — FETCH_BLOCKED 마커 대응 가이드가 시스템 프롬프트에 박혀 있다."""
    from simpleclaw.agent.orchestrator import _TOOL_USAGE_INSTRUCTION

    assert "FETCH_BLOCKED" in _TOOL_USAGE_INSTRUCTION
    # 재시도 금지 + graceful 대안 안내 키워드.
    assert "Do NOT retry" in _TOOL_USAGE_INSTRUCTION
