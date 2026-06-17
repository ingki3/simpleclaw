"""skill_validate 운영자 native tool 회귀 테스트."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.agent.skill_validate import handle_skill_validate
from simpleclaw.agent.tool_schemas import ToolScope, build_tool_definitions
from simpleclaw.llm.models import ToolCall


def _write_config(tmp_path, skills_dir):
    """테스트용 config.yaml을 작성한다."""
    config = tmp_path / "config.yaml"
    config.write_text(
        f"skills:\n  local_dir: {skills_dir}\n  global_dir: {tmp_path / 'global'}\n",
        encoding="utf-8",
    )
    return config


def _write_skill(skills_dir, name, skill_md, script_name="scripts/tool.py", script_body=None):
    """SKILL.md와 선택적 script fixture를 작성한다."""
    skill_dir = skills_dir / name
    script = skill_dir / script_name
    script.parent.mkdir(parents=True)
    if script_body is not None:
        script.write_text(script_body, encoding="utf-8")
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    return skill_dir, script


def test_skill_validate_is_not_exposed_to_runtime_context():
    """기본 runtime build에는 보이지 않고 operator/development gate가 열릴 때만 노출된다."""
    runtime_names = {tool.name for tool in build_tool_definitions(skills=[])}
    operator_names = {
        tool.name
        for tool in build_tool_definitions(
            skills=[],
            scopes=(ToolScope.RUNTIME, ToolScope.OPERATOR, ToolScope.DEVELOPMENT),
            operator_gate=True,
        )
    }

    assert "skill_validate" not in runtime_names
    assert "skill_validate" in operator_names


def test_skill_validate_discovered_skill_infers_script_without_smoke(tmp_path):
    """discovery된 skill은 command block에서 script_path를 추론하고 smoke=False면 실행하지 않는다."""
    skills_dir = tmp_path / "skills"
    side_effect = tmp_path / "touched"
    skill_dir, script = _write_skill(
        skills_dir,
        "market-skill",
        "---\nname: market-skill\ndescription: market helper\n---\n"
        "```bash\npython scripts/tool.py --help\n```\n",
        script_body=(
            "from pathlib import Path\n"
            f"Path({str(side_effect)!r}).write_text('ran')\n"
            "print('ok')\n"
        ),
    )
    config = _write_config(tmp_path, skills_dir)

    payload = json.loads(handle_skill_validate({"name": "market-skill"}, config_path=config))

    assert payload["ok"] is True
    assert payload["read_only"] is True
    assert payload["skill"]["skill_dir"] == str(skill_dir)
    assert payload["script"]["path"] == str(script)
    assert payload["script"]["exists"] is True
    assert payload["script"]["runner_exists"] is True
    assert payload["smoke_requested"] is False
    assert "smoke" not in payload
    assert not side_effect.exists()


def test_skill_validate_reports_missing_script_path_and_missing_file(tmp_path):
    """script_path 누락과 존재하지 않는 script는 명확한 error로 반환한다."""
    skills_dir = tmp_path / "skills"
    no_script = skills_dir / "no-script"
    no_script.mkdir(parents=True)
    (no_script / "SKILL.md").write_text("---\nname: no-script\n---\n", encoding="utf-8")
    _write_skill(
        skills_dir,
        "missing-file",
        "---\nname: missing-file\n---\n## Script\nTarget: `scripts/missing.py`\n",
        script_body=None,
    )
    config = _write_config(tmp_path, skills_dir)

    missing_path = json.loads(handle_skill_validate({"name": "no-script"}, config_path=config))
    missing_file = json.loads(handle_skill_validate({"name": "missing-file"}, config_path=config))

    assert missing_path["ok"] is False
    assert "no script path" in missing_path["errors"][0]
    assert missing_file["ok"] is False
    assert "script not found" in missing_file["errors"][0]


def test_skill_validate_prefers_adjacent_venv_python(tmp_path):
    """python skill 주변 venv가 있으면 smoke command runner로 해당 python을 표시한다."""
    skills_dir = tmp_path / "skills"
    _, script = _write_skill(
        skills_dir,
        "venv-skill",
        "---\nname: venv-skill\n---\n```bash\npython scripts/tool.py --help\n```\n",
        script_body="print('ok')\n",
    )
    venv_python = script.parent / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\n", encoding="utf-8")
    config = _write_config(tmp_path, skills_dir)

    payload = json.loads(handle_skill_validate({"name": "venv-skill"}, config_path=config))

    assert payload["ok"] is True
    assert payload["script"]["runner"] == str(venv_python)
    assert payload["script"]["runner_exists"] is True


def test_skill_validate_smoke_success_and_redacts_output(tmp_path):
    """smoke=True는 명시 args로 실행하고 stdout/stderr secret-like 값을 redaction한다."""
    skills_dir = tmp_path / "skills"
    _write_skill(
        skills_dir,
        "smoke-skill",
        "---\nname: smoke-skill\n---\n```bash\npython scripts/tool.py --help\n```\n",
        script_body=(
            "import sys\n"
            "print('credential_key=abc123 token:xyz Bearer live-token')\n"
            "print('secret=stderr-secret', file=sys.stderr)\n"
        ),
    )
    config = _write_config(tmp_path, skills_dir)

    payload = json.loads(
        handle_skill_validate(
            {"name": "smoke-skill", "smoke": True, "command_args": ["--help"]},
            config_path=config,
        )
    )

    assert payload["ok"] is True
    assert payload["smoke"]["exit_code"] == 0
    assert "abc123" not in payload["smoke"]["stdout"]
    assert "live-token" not in payload["smoke"]["stdout"]
    assert "stderr-secret" not in payload["smoke"]["stderr"]
    assert "[REDACTED]" in payload["smoke"]["stdout"]


def test_skill_validate_smoke_timeout_is_reported(tmp_path, monkeypatch):
    """smoke 실행이 timeout을 넘으면 프로세스 결과 대신 timeout error를 반환한다."""
    skills_dir = tmp_path / "skills"
    _write_skill(
        skills_dir,
        "slow-skill",
        "---\nname: slow-skill\n---\n```bash\npython scripts/tool.py --help\n```\n",
        script_body="import time\ntime.sleep(2)\n",
    )
    config = _write_config(tmp_path, skills_dir)
    monkeypatch.setattr("simpleclaw.agent.skill_validate._SMOKE_TIMEOUT_SECONDS", 0.1)

    payload = json.loads(handle_skill_validate({"name": "slow-skill", "smoke": True}, config_path=config))

    assert payload["ok"] is False
    assert payload["smoke"]["timed_out"] is True
    assert "timed out" in payload["errors"][0]


@pytest.mark.asyncio
async def test_orchestrator_skill_validate_dispatch_requires_operator_context(tmp_path, monkeypatch):
    """수동 dispatch도 operator context가 아니면 skill_validate를 실행하지 않는다."""
    config = _write_config(tmp_path, tmp_path / "skills")
    monkeypatch.setattr("simpleclaw.agent.orchestrator.create_router", MagicMock())
    monkeypatch.setattr(
        "simpleclaw.agent.tool_dispatch.handle_skill_validate",
        lambda args, **kwargs: json.dumps({"ok": True, "name": args["name"]}),
    )
    orchestrator = AgentOrchestrator(config)
    tool_call = ToolCall(id="skill-validate-1", name="skill_validate", arguments={"name": "market"})

    blocked = await orchestrator._dispatch_tool_call(tool_call)
    allowed = await orchestrator._dispatch_tool_call(tool_call, operator_tools=True)

    assert "operator context" in blocked
    assert json.loads(allowed) == {"ok": True, "name": "market"}


@pytest.mark.asyncio
async def test_process_operator_message_exposes_skill_validate_tool(tmp_path, monkeypatch):
    """operator message 경로는 LLMRequest tools에 skill_validate를 포함한다."""
    config = _write_config(tmp_path, tmp_path / "skills")
    monkeypatch.setattr("simpleclaw.agent.orchestrator.create_router", MagicMock())
    orchestrator = AgentOrchestrator(config)
    orchestrator._router = MagicMock()
    response = MagicMock()
    response.text = "ok"
    response.tool_calls = None
    orchestrator._router.send = AsyncMock(return_value=response)

    result = await orchestrator.process_operator_message("skill validate 확인")
    request = orchestrator._router.send.call_args.args[0]

    assert result == "ok"
    assert "skill_validate" in {tool.name for tool in request.tools}
