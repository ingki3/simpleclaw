"""skill_learning operator tool 의 review/승인/설치 UX 단위 테스트."""

from __future__ import annotations

import json

import pytest

from simpleclaw.agent.skill_learning_tool import handle_skill_learning
from simpleclaw.skills.learning import (
    SkillSuggestion,
    SkillSuggestionStore,
    SkillTraceStepSnapshot,
)


def _skill_md(name: str = "news-brief", body: str = "search then summarize") -> str:
    return (
        f"---\nname: {name}\ndescription: Summarize fresh news briefly.\n---\n"
        "# News Brief\n\n"
        "## When to Use\n- user asks for fresh news\n\n"
        f"## Procedure\n1. {body}\n\n"
        "## Verification\n- answer cites sources\n"
    )


def _make_suggestion(status: str = "pending", **overrides) -> SkillSuggestion:
    data = {
        "title": "News brief",
        "rationale": "Reusable search+summarize trace",
        "trace_fingerprint": "fp-1",
        "skill_name": "news-brief",
        "skill_md": _skill_md(),
        "scripts": {"scripts/run.py": "print('ok')\n"},
        "references": {"references/notes.md": "notes"},
        "source_msg_ids": [1, 2],
        "trace": [
            SkillTraceStepSnapshot("web_search", {}, "ok", True),
            SkillTraceStepSnapshot("web_fetch", {}, "ok", True),
        ],
        "risk_flags": ["network"],
    }
    data.update(overrides)
    suggestion = SkillSuggestion.new_pending(**data)
    suggestion.status = status
    return suggestion


@pytest.fixture
def env(tmp_path):
    store = SkillSuggestionStore(tmp_path / "suggestions.jsonl")
    config = {
        "suggestions_file": str(tmp_path / "suggestions.jsonl"),
        "target_dir": str(tmp_path / "skills"),
        "require_operator_accept": True,
    }
    skills_config = {"local_dir": str(tmp_path / "local_skills")}
    return store, config, skills_config


def _call(config, skills_config, **args) -> str:
    return handle_skill_learning(args, config=config, skills_config=skills_config)


def test_list_returns_review_summary(env):
    store, config, skills_config = env
    store.save_all([_make_suggestion()])

    out = json.loads(_call(config, skills_config, action="list"))

    assert len(out) == 1
    entry = out[0]
    assert entry["skill_name"] == "news-brief"
    assert entry["risk_flags"] == ["network"]
    assert entry["validation_errors"] == []
    assert entry["files_to_write"] == [
        "SKILL.md",
        "scripts/run.py",
        "references/notes.md",
    ]


def test_show_returns_review_friendly_details(env):
    store, config, skills_config = env
    suggestion = _make_suggestion()
    store.save_all([suggestion])

    out = json.loads(_call(config, skills_config, action="show", id=suggestion.id))

    assert out["skill_md"].startswith("---\nname: news-brief")
    assert out["skill_md_truncated"] is False
    assert {f["path"] for f in out["files_to_write"]} == {
        "SKILL.md",
        "scripts/run.py",
        "references/notes.md",
    }
    trace_summary = out["source_trace_summary"]
    assert trace_summary["steps"] == 2
    assert trace_summary["tools"][0] == {"tool_name": "web_search", "success": True}
    assert trace_summary["source_msg_ids"] == [1, 2]
    assert out["risk_flags"] == ["network"]
    assert out["validation_errors"] == []
    # 파일 본문 원문은 show 에 포함하지 않는다 — diff 로 확인.
    assert "scripts" not in out


def test_accept_marks_suggestion_accepted(env):
    store, config, skills_config = env
    suggestion = _make_suggestion()
    store.save_all([suggestion])

    out = json.loads(_call(config, skills_config, action="accept", id=suggestion.id))

    assert out["status"] == "accepted"
    assert store.get(suggestion.id).status == "accepted"


def test_reject_stores_reason(env):
    store, config, skills_config = env
    suggestion = _make_suggestion()
    store.save_all([suggestion])

    out = json.loads(
        _call(
            config,
            skills_config,
            action="reject",
            id=suggestion.id,
            reason="위험 스크립트 포함",
        )
    )

    assert out["status"] == "rejected"
    assert store.get(suggestion.id).reject_reason == "위험 스크립트 포함"


def test_materialize_rejects_non_accepted_suggestion(env, tmp_path):
    store, config, skills_config = env
    suggestion = _make_suggestion(status="pending")
    store.save_all([suggestion])

    out = _call(
        config, skills_config, action="materialize", id=suggestion.id, confirm=True
    )

    assert out.startswith("Error:")
    assert "accepted" in out
    assert not (tmp_path / "skills" / "news-brief").exists()


def test_materialize_requires_confirm(env, tmp_path):
    store, config, skills_config = env
    suggestion = _make_suggestion(status="accepted")
    store.save_all([suggestion])

    out = _call(config, skills_config, action="materialize", id=suggestion.id)

    assert out.startswith("Error:")
    assert "confirm=true" in out
    assert not (tmp_path / "skills" / "news-brief").exists()


def test_materialize_rejects_suggestions_with_validation_errors(env, tmp_path):
    store, config, skills_config = env
    suggestion = _make_suggestion(
        status="accepted", validation_errors=["SKILL.md is missing required section"]
    )
    store.save_all([suggestion])

    out = _call(
        config, skills_config, action="materialize", id=suggestion.id, confirm=True
    )

    assert out.startswith("Error:")
    assert "validation errors" in out


def test_materialize_accepted_with_confirm_installs_package(env, tmp_path):
    store, config, skills_config = env
    suggestion = _make_suggestion(status="accepted")
    store.save_all([suggestion])

    out = json.loads(
        _call(
            config, skills_config, action="materialize", id=suggestion.id, confirm=True
        )
    )

    assert out["status"] == "materialized"
    skill_dir = tmp_path / "skills" / "news-brief"
    assert (skill_dir / "SKILL.md").is_file()
    assert (skill_dir / "scripts" / "run.py").is_file()
    assert store.get(suggestion.id).status == "materialized"
    assert store.get(suggestion.id).materialized_path == str(skill_dir)


def test_diff_previews_create_and_update(env, tmp_path):
    store, config, skills_config = env
    suggestion = _make_suggestion(status="accepted")
    store.save_all([suggestion])

    created = json.loads(_call(config, skills_config, action="diff", id=suggestion.id))
    assert created["mode"] == "create"
    assert all(f["mode"] == "create" for f in created["files"])
    assert "+## Procedure" in next(
        f["diff"] for f in created["files"] if f["path"] == "SKILL.md"
    )

    # 설치 후 내용이 바뀐 후보의 diff 는 update preview 를 낸다.
    _call(config, skills_config, action="materialize", id=suggestion.id, confirm=True)
    changed = _make_suggestion(
        status="accepted",
        trace_fingerprint="fp-2",
        skill_md=_skill_md(body="changed procedure"),
    )
    store.save_all([store.get(suggestion.id), changed])

    updated = json.loads(_call(config, skills_config, action="diff", id=changed.id))
    assert updated["mode"] == "update"
    skill_md_entry = next(f for f in updated["files"] if f["path"] == "SKILL.md")
    assert skill_md_entry["mode"] == "update"
    assert "changed procedure" in skill_md_entry["diff"]
    unchanged = next(f for f in updated["files"] if f["path"] == "scripts/run.py")
    assert unchanged["mode"] == "unchanged"
    assert unchanged["diff"] == ""


def test_unknown_id_and_action_errors(env):
    _store, config, skills_config = env
    assert _call(config, skills_config, action="show", id="missing").startswith(
        "Error:"
    )
    assert _call(config, skills_config, action="show").startswith("Error:")
