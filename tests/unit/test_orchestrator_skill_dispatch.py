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
from simpleclaw.agent.system_prompts import load_system_prompt
from simpleclaw.llm.models import ToolCall
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


def test_skill_listing_instruction_bans_uvx():
    """skill listing 프롬프트에 uvx/pipx 금지 안내가 명시되어 있다.

    BIZ-166: 스킬 목록 프롬프트가 모델의 첫 시도를 registered skill 형태로 유도해야 한다.
    """
    skill_listing = load_system_prompt("skill_listing", refresh=True).prompt

    assert "uvx" in skill_listing
    assert "pipx" in skill_listing
    assert "execute_skill" in skill_listing


def test_skill_listing_instruction_limits_execute_skill_scope():
    """BIZ-363 — execute_skill 권장 용도는 스킬 설명과 전용 작업 중심이다."""
    skill_listing = load_system_prompt("skill_listing", refresh=True).prompt
    from simpleclaw.agent.orchestrator import _TOOL_USAGE_INSTRUCTION

    assert "numeric calculations" in skill_listing
    assert "data processing" in skill_listing
    assert "complex logic" in skill_listing
    assert "explicitly covered by the skill description" in skill_listing
    assert "Do NOT use skills as a generic shell escape" in _TOOL_USAGE_INSTRUCTION


def test_tool_usage_instruction_avoids_specific_browser_routing():
    """tool_usage 에 특정 web/browser 스킬 사용법을 박제하지 않는다."""
    from simpleclaw.agent.orchestrator import _TOOL_USAGE_INSTRUCTION

    assert "web_fetch" not in _TOOL_USAGE_INSTRUCTION
    assert "agent-browser" not in _TOOL_USAGE_INSTRUCTION
    assert "networkidle" not in _TOOL_USAGE_INSTRUCTION
    assert "wait --load load" not in _TOOL_USAGE_INSTRUCTION


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


@pytest.mark.asyncio
async def test_execute_registered_skill_preserves_quoted_args(
    config_file, tmp_path, monkeypatch,
):
    """BIZ-362 — 등록 skill args 파싱은 quoted query를 하나의 인자로 보존한다."""
    from types import SimpleNamespace

    orch = AgentOrchestrator(config_file)
    skill = _make_python_skill(tmp_path, "news-search-skill")
    orch._skills_by_name = {skill.name: skill}
    captured: dict[str, object] = {}

    async def fake_run_skill(skill_arg, args=None, timeout=60, *, metrics=None):
        captured["skill"] = skill_arg
        captured["args"] = args
        captured["timeout"] = timeout
        captured["metrics"] = metrics
        return SimpleNamespace(output="ok", success=True)

    monkeypatch.setattr(
        "simpleclaw.agent.skill_dispatch.run_skill",
        fake_run_skill,
    )

    result = await orch._execute_skill(
        "news-search-skill",
        '-q "US stock market closing report"',
    )

    assert result == "ok"
    assert captured["args"] == ["-q", "US stock market closing report"]


@pytest.mark.asyncio
async def test_execute_skill_prefers_registered_skill_when_command_also_present(
    config_file, tmp_path, monkeypatch,
):
    """BIZ-363 — skill_name 이 있으면 legacy command 보다 등록 skill 실행이 우선이다.

    2026-06-11 운영 로그에서 모델이 ``skill_name`` 과 ``command`` 를 동시에
    보냈고, 기존 dispatch 는 command 를 raw shell 로 먼저 실행해
    ``realtime-lookup-skill: command not found`` 를 만들었다. 등록 skill 이름이
    명시된 호출은 shell PATH 에 맡기지 말고 executor 경로로 보내야 한다.
    """
    from types import SimpleNamespace

    orch = AgentOrchestrator(config_file)
    skill = _make_python_skill(tmp_path, "realtime-lookup-skill")
    orch._skills_by_name = {skill.name: skill}
    command_calls: list[tuple[str, str]] = []
    captured: dict[str, object] = {}

    async def fake_command(skill_name, command):
        command_calls.append((skill_name, command))
        return "raw-shell"

    async def fake_run_skill(skill_arg, args=None, timeout=60, *, metrics=None):
        captured["skill"] = skill_arg
        captured["args"] = args
        return SimpleNamespace(output="registered-skill", success=True)

    monkeypatch.setattr(orch, "_execute_command", fake_command)
    monkeypatch.setattr(
        "simpleclaw.agent.skill_dispatch.run_skill",
        fake_run_skill,
    )

    result = await orch._dispatch_tool_call(
        ToolCall(
            id="c1",
            name="execute_skill",
            arguments={
                "skill_name": "realtime-lookup-skill",
                "command": 'realtime-lookup-skill "2026 북중미 월드컵 일정"',
                "args": "2026 북중미 월드컵 일정",
            },
        )
    )

    assert result == "registered-skill"
    assert command_calls == []
    assert captured["skill"] == skill
    assert captured["args"] == ["2026", "북중미", "월드컵", "일정"]


@pytest.mark.asyncio
async def test_registered_python_skill_prefers_adjacent_venv_python(
    tmp_path, monkeypatch,
):
    """BIZ-362 — .py skill은 런타임 Python 대신 skill-local venv를 우선 실행한다."""
    from simpleclaw.skills.executor import execute_skill

    skill = _make_python_skill(tmp_path, "us-stock-skill")
    expected_python = Path(skill.skill_dir) / "venv" / "bin" / "python"
    expected_script = Path(skill.script_path)
    captured_cmd: list[str] = []
    captured_cwd: str | None = None

    class _FakeProc:
        returncode = 0

        async def communicate(self):
            return b"ok", b""

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        nonlocal captured_cmd, captured_cwd
        captured_cmd = list(cmd)
        captured_cwd = kwargs.get("cwd")
        return _FakeProc()

    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    result = await execute_skill(skill, ["info", "--symbol", "NVDA"], timeout=5)

    assert result.output == "ok"
    assert captured_cmd[:2] == [str(expected_python), str(expected_script)]
    assert captured_cmd[2:] == ["info", "--symbol", "NVDA"]
    assert captured_cwd == skill.skill_dir


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


def test_tool_usage_instruction_does_not_embed_agent_browser_chain_manual():
    """BIZ-187 — composite chain 세부 매뉴얼은 tool_usage 에 넣지 않는다."""
    from simpleclaw.agent.orchestrator import _TOOL_USAGE_INSTRUCTION

    assert "separate turns" not in _TOOL_USAGE_INSTRUCTION
    assert "&&" not in _TOOL_USAGE_INSTRUCTION


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


@pytest.mark.asyncio
async def test_execute_command_agent_browser_command_not_found_falls_back_to_npx(
    config_file, monkeypatch,
):
    """BIZ-337 — bare ``agent-browser`` PATH 실패 시 npx runner 로 1회 재시도."""
    from simpleclaw.agent import AgentOrchestrator

    orch = AgentOrchestrator(config_file)
    seen_commands: list[str] = []

    class _FakeProc:
        def __init__(self, returncode: int, stdout: bytes, stderr: bytes):
            self.returncode = returncode
            self._stdout = stdout
            self._stderr = stderr

        async def communicate(self):
            return self._stdout, self._stderr

    async def fake_create(command, *args, **kwargs):
        seen_commands.append(command)
        if command == "agent-browser get text body":
            return _FakeProc(
                127,
                b"",
                b"/bin/sh: agent-browser: command not found",
            )
        if command == "npx --yes agent-browser get text body":
            return _FakeProc(0, b"browser text", b"")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("asyncio.create_subprocess_shell", fake_create)

    result = await orch._execute_command(
        "agent-browser", "agent-browser get text body",
    )

    assert result == "browser text"
    assert seen_commands == [
        "agent-browser get text body",
        "npx --yes agent-browser get text body",
    ]


@pytest.mark.asyncio
async def test_execute_command_non_agent_browser_command_not_found_does_not_fallback(
    config_file, monkeypatch,
):
    """BIZ-337 — agent-browser 외 command-not-found 는 npx fallback 대상이 아니다."""
    from simpleclaw.agent import AgentOrchestrator

    orch = AgentOrchestrator(config_file)
    seen_commands: list[str] = []

    class _FakeProc:
        returncode = 127

        async def communicate(self):
            return b"", b"/bin/sh: news-search-skill: command not found"

    async def fake_create(command, *args, **kwargs):
        seen_commands.append(command)
        return _FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_shell", fake_create)

    result = await orch._execute_command(
        "news-search-skill", "news-search-skill query",
    )

    assert seen_commands == ["news-search-skill query"]
    assert "Command failed" in result
    assert "command not found" in result


@pytest.mark.asyncio
async def test_execute_command_agent_browser_fallback_failure_guides_no_manual_search(
    config_file, monkeypatch,
):
    """BIZ-337 — fallback 도 실패하면 수동 검색 요구 금지 안내를 tool result 에 넣는다."""
    from simpleclaw.agent import AgentOrchestrator

    orch = AgentOrchestrator(config_file)

    class _FakeProc:
        returncode = 127

        async def communicate(self):
            return b"", b"/bin/sh: agent-browser: command not found"

    async def fake_create(command, *args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_shell", fake_create)

    result = await orch._execute_command(
        "agent-browser", "agent-browser get text body",
    )

    assert "[TOOL_ERROR]" in result
    assert "Do not ask the user to search manually" in result
    assert "separate verified facts from unverified facts" in result


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


def test_tool_usage_instruction_excludes_fetch_blocked_tool_manual():
    """BIZ-190 — FETCH_BLOCKED 세부 대응은 tool_usage 에 박제하지 않는다."""
    from simpleclaw.agent.orchestrator import _TOOL_USAGE_INSTRUCTION

    assert "FETCH_BLOCKED" not in _TOOL_USAGE_INSTRUCTION
    assert "Do NOT retry" not in _TOOL_USAGE_INSTRUCTION
