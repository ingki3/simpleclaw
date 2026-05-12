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
