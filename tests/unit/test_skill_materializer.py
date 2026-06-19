"""skill materializer 단위 테스트."""

from __future__ import annotations

import pytest

from simpleclaw.skills.learning import SkillSuggestion
from simpleclaw.skills.materializer import materialize_skill_suggestion


def make_suggestion(**overrides):
    data = {
        "title": "T",
        "rationale": "R",
        "trace_fingerprint": "f",
        "skill_name": "demo-skill",
        "skill_md": "---\nname: demo-skill\ndescription: Demo skill.\n---\n# Demo Skill\n",
        "scripts": {"scripts/demo.py": "print('ok')\n"},
        "source_msg_ids": [1, 2],
    }
    data.update(overrides)
    return SkillSuggestion.new_pending(**data)


def test_materialize_creates_skill_package(tmp_path):
    suggestion = make_suggestion()

    result = materialize_skill_suggestion(suggestion, target_dir=tmp_path)

    assert (tmp_path / "demo-skill" / "SKILL.md").is_file()
    assert (tmp_path / "demo-skill" / "scripts" / "demo.py").is_file()
    assert result.skill_dir == tmp_path / "demo-skill"


def test_materialize_rejects_path_traversal(tmp_path):
    suggestion = make_suggestion(scripts={"../escape.py": "print('bad')"})
    with pytest.raises(ValueError):
        materialize_skill_suggestion(suggestion, target_dir=tmp_path)


def test_materialize_refuses_overwrite_by_default(tmp_path):
    suggestion = make_suggestion()
    materialize_skill_suggestion(suggestion, target_dir=tmp_path)
    with pytest.raises(FileExistsError):
        materialize_skill_suggestion(suggestion, target_dir=tmp_path)


def test_generated_python_script_syntax_checked(tmp_path):
    suggestion = make_suggestion(scripts={"scripts/bad.py": "if True print('bad')"})
    with pytest.raises(SyntaxError):
        materialize_skill_suggestion(suggestion, target_dir=tmp_path)
