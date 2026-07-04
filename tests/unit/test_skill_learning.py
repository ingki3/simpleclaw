"""skill learning 후보 저장/검증 단위 테스트."""

from __future__ import annotations

from simpleclaw.agent.tool_loop import ToolTraceStep
from simpleclaw.skills.learning import (
    SkillSuggestion,
    SkillSuggestionStore,
    build_skill_candidate_prompt,
    is_complex_successful_trace,
    validate_skill_package_plan,
)


def _skill_md(name: str = "demo-skill") -> str:
    return f"---\nname: {name}\ndescription: Demo skill.\n---\n# Demo\n"


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


def test_validate_skill_package_rejects_secret_like_content():
    errors = validate_skill_package_plan(
        skill_md=_skill_md(),
        scripts={"scripts/demo.py": "TOKEN='abcdef1234567890'"},
    )
    assert errors
