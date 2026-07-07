"""skill learning 후보 저장/검증 단위 테스트."""

from __future__ import annotations

from simpleclaw.agent.tool_loop import ToolTraceStep
from simpleclaw.skills.learning import (
    MAX_SKILL_DESCRIPTION_CHARS,
    REQUIRED_SKILL_MD_SECTIONS,
    SKILL_MD_SECTION_ORDER,
    SKILL_SUGGESTION_RESPONSE_SCHEMA,
    SkillSuggestion,
    SkillSuggestionStore,
    build_skill_candidate_prompt,
    is_complex_successful_trace,
    suggestion_from_candidate_payload,
    validate_skill_package_plan,
)


def _skill_md(name: str = "demo-skill", description: str = "Demo skill.") -> str:
    """authoring standards 를 통과하는 최소 SKILL.md."""
    return (
        f"---\nname: {name}\ndescription: {description}\n---\n"
        "# Demo\n\n"
        "## When to Use\n- demo scenario\n\n"
        "## Procedure\n1. run the demo\n\n"
        "## Verification\n- confirm output\n"
    )


def test_store_upserts_pending_by_trace_fingerprint(tmp_path):
    store = SkillSuggestionStore(tmp_path / "skill_suggestions.jsonl")
    suggestion = SkillSuggestion.new_pending(
        title="뉴스 검색 요약 스킬",
        rationale="반복 가능한 뉴스 검색+요약 trace",
        trace_fingerprint="abc123",
        skill_name="news-summary-skill",
        skill_md=_skill_md("news-summary-skill"),
        scripts={"scripts/news_summary.py": "print('ok')\n"},
        source_msg_ids=[1, 2],
    )

    first = store.upsert_pending(suggestion)
    second = store.upsert_pending(suggestion)

    assert first.id == second.id
    assert len(store.list_pending()) == 1


def test_complex_success_requires_distinct_tools():
    trace = [
        ToolTraceStep(tool_name="web_search", arguments={}, observation_preview="ok"),
        ToolTraceStep(tool_name="web_fetch", arguments={}, observation_preview="ok"),
    ]
    assert is_complex_successful_trace(
        trace, final_text="x" * 600, min_tool_calls=2, min_distinct_tools=2
    )


def test_complex_success_rejects_error_observation():
    trace = [
        ToolTraceStep(
            tool_name="web_search",
            arguments={},
            observation_preview="Error: timeout",
            success=False,
        )
    ]
    assert not is_complex_successful_trace(
        trace, final_text="x" * 600, min_tool_calls=1, min_distinct_tools=1
    )


def test_build_skill_candidate_prompt_contains_safety_rules():
    prompt = build_skill_candidate_prompt(user_text="u", assistant_text="a", trace=[])
    assert "do not include secrets" in prompt.lower()
    assert "SKILL.md" in prompt
    assert "JSON" in prompt


def test_build_skill_candidate_prompt_contains_authoring_standards():
    """Hermes /learn 기준 authoring standards 가 프롬프트에 포함된다."""
    prompt = build_skill_candidate_prompt(user_text="u", assistant_text="a", trace=[])
    assert f"at most {MAX_SKILL_DESCRIPTION_CHARS} characters" in prompt
    assert ", ".join(SKILL_MD_SECTION_ORDER) in prompt
    assert "Never invent commands" in prompt
    assert "scripts/" in prompt
    assert "references/" in prompt
    assert "templates/" in prompt
    assert "platform" in prompt
    # native/wrapped tool 은 tool 이름으로 설명, shell 유틸 남발 금지.
    assert "tool name" in prompt
    assert "shell utilities" in prompt


def test_skill_suggestion_response_schema_shape():
    """structured output schema 는 required/additionalProperties/순서가 안정적이다."""
    schema = SKILL_SUGGESTION_RESPONSE_SCHEMA
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    expected = [
        "title",
        "rationale",
        "skill_name",
        "skill_md",
        "scripts",
        "references",
        "risk_flags",
    ]
    assert schema["required"] == expected
    assert schema["propertyOrdering"] == expected
    assert set(schema["properties"].keys()) == set(expected)
    entry = schema["properties"]["scripts"]["items"]
    assert entry["required"] == ["path", "content"]
    assert entry["additionalProperties"] is False


def test_suggestion_from_candidate_payload_accepts_entry_arrays():
    """schema-shaped {path, content} 배열이 dict 매핑으로 정규화된다."""
    payload = {
        "title": "News brief",
        "rationale": "Reusable trace",
        "skill_name": "news-brief",
        "skill_md": _skill_md("news-brief"),
        "scripts": [{"path": "scripts/run.py", "content": "print('ok')\n"}],
        "references": [{"path": "references/notes.md", "content": "notes"}],
        "risk_flags": ["network"],
    }
    suggestion = suggestion_from_candidate_payload(
        payload, trace_fingerprint_value="fp", source_msg_ids=[1], trace=[]
    )
    assert suggestion.scripts == {"scripts/run.py": "print('ok')\n"}
    assert suggestion.references == {"references/notes.md": "notes"}
    assert suggestion.skill_name == "news-brief"
    assert "network" in suggestion.risk_flags
    assert suggestion.validation_errors == []
    assert suggestion.status == "pending"


def test_suggestion_from_candidate_payload_accepts_legacy_dict():
    payload = {
        "title": "News brief",
        "skill_name": "news-brief",
        "skill_md": _skill_md("news-brief"),
        "scripts": {"scripts/run.py": "print('ok')\n"},
    }
    suggestion = suggestion_from_candidate_payload(
        payload, trace_fingerprint_value="fp", source_msg_ids=[], trace=[]
    )
    assert suggestion.scripts == {"scripts/run.py": "print('ok')\n"}


def test_validate_skill_package_rejects_secret_like_content():
    errors = validate_skill_package_plan(
        skill_md=_skill_md(),
        scripts={"scripts/demo.py": "TOKEN='abcdef1234567890'"},
    )
    assert any("Secret-like" in e for e in errors)


def test_validate_skill_package_accepts_valid_plan():
    errors = validate_skill_package_plan(
        skill_name="demo-skill",
        skill_md=_skill_md(),
        scripts={"scripts/demo.py": "print('ok')\n"},
        references={"references/notes.md": "notes", "templates/t.md": "t"},
    )
    assert errors == []


def test_validate_skill_package_rejects_long_description():
    long_description = "x" * (MAX_SKILL_DESCRIPTION_CHARS + 1)
    errors = validate_skill_package_plan(
        skill_md=_skill_md(description=long_description)
    )
    assert any("description exceeds" in e for e in errors)


def test_validate_skill_package_rejects_missing_description():
    md = (
        "---\nname: demo-skill\n---\n# Demo\n\n"
        "## When to Use\n- x\n\n## Procedure\n1. x\n\n## Verification\n- x\n"
    )
    errors = validate_skill_package_plan(skill_md=md)
    assert any("description" in e for e in errors)


def test_validate_skill_package_rejects_missing_frontmatter():
    errors = validate_skill_package_plan(skill_md="# Demo\nno frontmatter\n")
    assert any("frontmatter" in e for e in errors)


def test_validate_skill_package_rejects_name_mismatch():
    errors = validate_skill_package_plan(
        skill_name="other-skill", skill_md=_skill_md("demo-skill")
    )
    assert any("does not match" in e for e in errors)


def test_validate_skill_package_requires_core_sections():
    md = "---\nname: demo-skill\ndescription: Demo skill.\n---\n# Demo\nno sections\n"
    errors = validate_skill_package_plan(skill_md=md)
    for section in REQUIRED_SKILL_MD_SECTIONS:
        assert any(section in e for e in errors)


def test_validate_skill_package_rejects_unsafe_paths():
    errors = validate_skill_package_plan(
        skill_md=_skill_md(),
        scripts={"scripts/../../escape.py": "print('bad')\n"},
    )
    assert any("Unsafe relative path" in e for e in errors)


def test_validate_skill_package_enforces_script_prefix():
    errors = validate_skill_package_plan(
        skill_md=_skill_md(),
        scripts={"tools/run.py": "print('ok')\n"},
    )
    assert any("must start with scripts/" in e for e in errors)


def test_validate_skill_package_enforces_reference_prefix():
    errors = validate_skill_package_plan(
        skill_md=_skill_md(),
        references={"docs/notes.md": "notes"},
    )
    assert any("references/ or templates/" in e for e in errors)
