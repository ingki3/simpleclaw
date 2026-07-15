"""운영자용 runtime skill 독립 검증 도구.

``skill_validate``는 SimpleClaw runtime skill의 discovery 결과와 실행 경계를
read-only로 점검한다. 기본 모드는 SKILL.md 파싱, ``script_path`` 추론 결과,
스크립트/인터프리터 존재 여부만 확인하고, ``smoke=True``일 때만 짧은
``--help`` subprocess smoke를 수행한다.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from simpleclaw.security import filter_env
from simpleclaw.skills.discovery import discover_skills
from simpleclaw.skills.executor import _build_command
from simpleclaw.skills.models import SkillDefinition

DEFAULT_CONFIG_PATH = Path("/Users/simplist/.simpleclaw/config.yaml")
_DEFAULT_SKILLS_CONFIG = {
    "local_dir": ".agent/skills",
    "global_dir": "~/.agents/skills",
}
_SMOKE_TIMEOUT_SECONDS = 5
_OUTPUT_LIMIT = 1000
_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|credential[_-]?key|token|secret|password)(\s*[=:]\s*)([^\s'\"]+)"),
    re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+"),
)


def handle_skill_validate(
    args: dict[str, Any],
    *,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    skills: list[SkillDefinition] | None = None,
) -> str:
    """Function Calling 핸들러용 JSON 문자열을 반환한다.

    Args:
        args: ``name``과 선택적 ``smoke``/``command_args``를 담은 tool arguments.
        config_path: live ``config.yaml`` 경로. ``skills.local_dir/global_dir`` resolve
            기준으로 쓴다.
        skills: 이미 hot-reload된 runtime skill 목록. 없으면 config 기준으로 재탐색한다.

    Returns:
        discovery/SKILL.md/script/smoke 진단 결과. 실패도 예외 대신 ``ok=false``
        JSON으로 반환해 운영자가 바로 원인을 볼 수 있게 한다.
    """
    config = Path(config_path).expanduser()
    raw_config, config_error = _read_config(config)
    skills_config = _skills_config(raw_config)
    source_dirs = {
        "local": str(Path(skills_config["local_dir"]).expanduser()),
        "global": str(Path(skills_config["global_dir"]).expanduser()),
    }
    payload: dict[str, Any] = {
        "ok": False,
        "read_only": True,
        "config_path": str(config),
        "skills_dirs": source_dirs,
        "smoke_requested": bool(args.get("smoke", False)),
        "errors": [],
        "warnings": [],
    }
    if config_error:
        payload["warnings"].append(config_error)

    name = str(args.get("name") or "").strip()
    if not name:
        payload["errors"].append("name is required")
        return _dump(payload)

    discovered = skills
    if discovered is None:
        try:
            discovered = discover_skills(source_dirs["local"], source_dirs["global"])
        except Exception as exc:  # noqa: BLE001 — 진단 도구는 discovery 실패를 JSON화한다.
            payload["errors"].append(f"skill discovery failed: {exc}")
            return _dump(payload)

    skill = _find_skill(name, discovered)
    if skill is None:
        payload["errors"].append(f"skill not found: {name}")
        payload["discovered_names"] = sorted(item.name for item in discovered)
        return _dump(payload)

    script_info = _script_info(skill)
    payload["skill"] = _skill_summary(skill)
    payload["script"] = script_info

    if not script_info["path"]:
        payload["errors"].append(f"Skill '{skill.name}' has no script path defined")
    elif not script_info["exists"]:
        payload["errors"].append(f"script not found: {script_info['path']}")
    elif not script_info["runner_exists"]:
        payload["errors"].append(f"runner not found: {script_info['runner']}")

    if payload["errors"]:
        return _dump(payload)

    if payload["smoke_requested"]:
        payload["smoke"] = _smoke(skill, _normalize_command_args(args.get("command_args")))
        if not payload["smoke"]["ok"]:
            payload["errors"].append(payload["smoke"].get("error", "smoke failed"))

    payload["ok"] = not payload["errors"]
    return _dump(payload)


def _read_config(path: Path) -> tuple[dict[str, Any], str | None]:
    """config.yaml을 읽고 실패 시 빈 dict와 warning 문자열을 반환한다."""
    if not path.is_file():
        return {}, f"config file not found: {path}; using default skills dirs"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return {}, f"config load failed: {exc}; using default skills dirs"
    if not isinstance(data, dict):
        return {}, "config root is not a mapping; using default skills dirs"
    return data, None


def _skills_config(raw_config: dict[str, Any]) -> dict[str, str]:
    """raw config에서 skills directory 설정을 기본값으로 보강한다."""
    configured = raw_config.get("skills", {})
    if not isinstance(configured, dict):
        configured = {}
    merged = deepcopy(_DEFAULT_SKILLS_CONFIG)
    merged.update({key: configured.get(key, default) for key, default in merged.items()})
    return merged


def _find_skill(name: str, skills: list[SkillDefinition]) -> SkillDefinition | None:
    """이름이 정확히 일치하는 runtime skill을 반환한다."""
    for skill in skills:
        if skill.name == name:
            return skill
    return None


def _skill_summary(skill: SkillDefinition) -> dict[str, Any]:
    """검증 대상 skill의 discovery metadata를 요약한다."""
    scope = skill.scope.value if hasattr(skill.scope, "value") else str(skill.scope)
    return {
        "name": skill.name,
        "description": skill.description,
        "source": "simpleclaw_runtime_skill",
        "scope": scope,
        "skill_dir": str(Path(skill.skill_dir).expanduser()) if skill.skill_dir else "",
        "commands_count": len(skill.commands or []),
        "has_retry_policy": skill.retry_policy is not None,
    }


def _script_info(skill: SkillDefinition) -> dict[str, Any]:
    """script_path와 실행 runner 존재 여부를 반환한다."""
    if not skill.script_path:
        return {
            "path": "",
            "exists": False,
            "suffix": "",
            "runner": "",
            "runner_exists": False,
            "command": [],
        }

    script = Path(skill.script_path).expanduser()
    command = _build_command(script)
    runner = command[0]
    return {
        "path": str(script),
        "exists": script.is_file(),
        "suffix": script.suffix.lower(),
        "runner": runner,
        "runner_exists": _runner_exists(runner),
        "command": command,
    }


def _runner_exists(runner: str) -> bool:
    """명령 runner가 절대/상대 경로 또는 PATH에서 실행 가능한지 확인한다."""
    path = Path(runner).expanduser()
    if path.is_absolute() or len(path.parts) > 1:
        return path.is_file()
    return shutil.which(runner) is not None


def _normalize_command_args(raw: object) -> list[str]:
    """command_args를 문자열 리스트로 정규화한다. 생략 시 ``--help``를 사용한다."""
    if raw is None:
        return ["--help"]
    if not isinstance(raw, list):
        return [str(raw)]
    return [str(item) for item in raw]


def _smoke(skill: SkillDefinition, command_args: list[str]) -> dict[str, Any]:
    """짧은 subprocess smoke를 실행하고 stdout/stderr를 redaction해 반환한다."""
    script = Path(skill.script_path).expanduser()
    command = [*_build_command(script), *command_args]
    try:
        completed = subprocess.run(  # noqa: S603 — operator-gated smoke이며 shell=False로 실행한다.
            command,
            cwd=skill.skill_dir or None,
            text=True,
            capture_output=True,
            timeout=_SMOKE_TIMEOUT_SECONDS,
            check=False,
            # BIZ-443: smoke도 runtime skill 실행과 같은 env scrub 정책을 따른다
            env=filter_env(),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "timed_out": True,
            "timeout_seconds": _SMOKE_TIMEOUT_SECONDS,
            "command_args": command_args,
            "stdout": _redact(exc.stdout or ""),
            "stderr": _redact(exc.stderr or ""),
            "error": f"smoke timed out after {_SMOKE_TIMEOUT_SECONDS}s",
        }
    except OSError as exc:
        return {
            "ok": False,
            "timed_out": False,
            "command_args": command_args,
            "error": f"smoke failed to start: {exc}",
        }

    return {
        "ok": completed.returncode == 0,
        "timed_out": False,
        "timeout_seconds": _SMOKE_TIMEOUT_SECONDS,
        "command_args": command_args,
        "exit_code": completed.returncode,
        "stdout": _redact(completed.stdout),
        "stderr": _redact(completed.stderr),
    }


def _redact(text: str) -> str:
    """smoke 출력에서 대표 secret 패턴을 마스킹하고 길이를 제한한다."""
    redacted = text
    for index, pattern in enumerate(_SECRET_PATTERNS):
        if index == 1:
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
        else:
            redacted = pattern.sub(r"\1\2[REDACTED]", redacted)
    if len(redacted) > _OUTPUT_LIMIT:
        return redacted[:_OUTPUT_LIMIT] + "…[truncated]"
    return redacted


def _dump(payload: dict[str, Any]) -> str:
    """JSON 직렬화 옵션을 한 곳에 모은다."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


__all__ = ["DEFAULT_CONFIG_PATH", "handle_skill_validate"]