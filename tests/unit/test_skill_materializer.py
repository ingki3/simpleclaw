"""skill materializer 단위 테스트."""

from __future__ import annotations

import pytest

from simpleclaw.skills.learning import SkillSuggestion
from simpleclaw.skills.materializer import materialize_skill_suggestion


def _skill_md(name: str = "demo-skill", body: str = "run the demo") -> str:
    return (
        f"---\nname: {name}\ndescription: Demo skill.\n---\n"
        "# Demo\n\n"
        "## When to Use\n- demo scenario\n\n"
        f"## Procedure\n1. {body}\n\n"
        "## Verification\n- confirm output\n"
    )


def make_suggestion(status: str = "accepted", **overrides):
    data = {
        "title": "T",
        "rationale": "R",
        "trace_fingerprint": "f",
        "skill_name": "demo-skill",
        "skill_md": _skill_md(),
        "scripts": {"scripts/demo.py": "print('ok')\n"},
        "source_msg_ids": [1, 2],
    }
    data.update(overrides)
    suggestion = SkillSuggestion.new_pending(**data)
    suggestion.status = status
    return suggestion


def test_materialize_creates_skill_package(tmp_path):
    suggestion = make_suggestion()

    result = materialize_skill_suggestion(suggestion, target_dir=tmp_path)

    assert (tmp_path / "demo-skill" / "SKILL.md").is_file()
    assert (tmp_path / "demo-skill" / "scripts" / "demo.py").is_file()
    assert result.skill_dir == tmp_path / "demo-skill"
    assert result.backup_dir is None


def test_materialize_rejects_unaccepted_suggestion(tmp_path):
    suggestion = make_suggestion(status="pending")
    with pytest.raises(PermissionError):
        materialize_skill_suggestion(suggestion, target_dir=tmp_path)
    assert not (tmp_path / "demo-skill").exists()


def test_materialize_allows_explicit_accept_opt_out(tmp_path):
    suggestion = make_suggestion(status="pending")
    result = materialize_skill_suggestion(
        suggestion, target_dir=tmp_path, require_accepted=False
    )
    assert result.skill_dir.is_dir()


def test_materialize_rejects_path_traversal(tmp_path):
    suggestion = make_suggestion(scripts={"../escape.py": "print('bad')"})
    with pytest.raises(ValueError):
        materialize_skill_suggestion(suggestion, target_dir=tmp_path)


def test_materialize_rejects_script_outside_scripts_dir(tmp_path):
    suggestion = make_suggestion(scripts={"tools/run.py": "print('ok')\n"})
    with pytest.raises(ValueError):
        materialize_skill_suggestion(suggestion, target_dir=tmp_path)


def test_materialize_rejects_reference_outside_allowed_dirs(tmp_path):
    suggestion = make_suggestion(
        scripts={}, references={"docs/notes.md": "notes"}
    )
    with pytest.raises(ValueError):
        materialize_skill_suggestion(suggestion, target_dir=tmp_path)


def test_materialize_refuses_overwrite_by_default(tmp_path):
    suggestion = make_suggestion()
    materialize_skill_suggestion(suggestion, target_dir=tmp_path)
    with pytest.raises(FileExistsError):
        materialize_skill_suggestion(suggestion, target_dir=tmp_path)


def test_materialize_overwrite_backs_up_previous_package(tmp_path):
    old = make_suggestion(skill_md=_skill_md(body="old procedure"))
    materialize_skill_suggestion(old, target_dir=tmp_path)

    new = make_suggestion(skill_md=_skill_md(body="new procedure"))
    result = materialize_skill_suggestion(new, target_dir=tmp_path, overwrite=True)

    installed = (tmp_path / "demo-skill" / "SKILL.md").read_text(encoding="utf-8")
    assert "new procedure" in installed
    assert result.backup_dir is not None and result.backup_dir.is_dir()
    backed_up = (result.backup_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "old procedure" in backed_up


def test_generated_python_script_syntax_checked(tmp_path):
    suggestion = make_suggestion(scripts={"scripts/bad.py": "if True print('bad')"})
    with pytest.raises(SyntaxError):
        materialize_skill_suggestion(suggestion, target_dir=tmp_path)
