"""Runtime path contracts for SimpleClaw.

실제 `/Users/simplist` live 파일을 읽지 않는다. 대신 config/default 문자열과
expanduser 기반 경로 규약이 repo code에서 서로 어긋나지 않는지 검증한다.
"""

from __future__ import annotations

from pathlib import Path

from simpleclaw.agent.commands import try_recipe_command
from simpleclaw.config_sections.study import _STUDY_DEFAULTS


def test_study_wiki_default_path_contract():
    """Study wiki default는 real HOME 기준 runtime data 위치여야 한다."""
    assert _STUDY_DEFAULTS["wiki_dir"] == "~/.simpleclaw-agent/default/agent_wiki"


def test_recipe_command_default_path_contract():
    """Slash recipe discovery default는 live recipes.dir 규약과 일치한다."""
    defaults = try_recipe_command.__defaults__
    assert defaults is not None
    default_recipes_dir = str(defaults[0])
    assert default_recipes_dir == "~/.simpleclaw-agent/default/recipes"


def test_live_path_strings_are_home_relative_not_profile_relative():
    """Runtime data 기본 경로는 Hermes profile 내부 shadow HOME을 가정하지 않는다."""
    paths = [
        "~/.simpleclaw-agent/default/recipes",
        "~/.simpleclaw-agent/default/agent_wiki",
    ]
    for value in paths:
        expanded = Path(value).expanduser()
        assert ".hermes/profiles" not in str(expanded)
