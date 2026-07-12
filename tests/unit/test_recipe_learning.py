"""RecipeSuggestion 모델/저장소/검증 단위 테스트 (BIZ-428, BIZ-435)."""

from __future__ import annotations

import pytest
import yaml

from simpleclaw.recipes.learning import (
    RECIPE_RISK_FLAG_ALLOWLIST,
    RECIPE_SUGGESTION_RESPONSE_SCHEMA,
    RecipeSuggestion,
    RecipeSuggestionStore,
    build_recipe_candidate_prompt,
    detect_trace_risk_flags,
    normalize_recipe_name,
    normalize_recipe_risk_flags,
    suggestion_from_recipe_payload,
    validate_recipe_suggestion_plan,
)
from simpleclaw.skills.learning import RISK_FLAG_ALLOWLIST, SkillTraceStepSnapshot


def _recipe_yaml(name: str = "demo-recipe") -> str:
    return yaml.safe_dump(
        {
            "name": name,
            "description": "데모 레시피",
            "instructions": "{{ query }}로 확인해줘.\n",
        },
        allow_unicode=True,
        sort_keys=False,
    )


def _suggestion(fingerprint: str = "fp-1", name: str = "demo-recipe") -> RecipeSuggestion:
    return RecipeSuggestion.new_pending(
        title="데모 워크플로",
        rationale="반복 가능한 조회 절차",
        trace_fingerprint=fingerprint,
        recipe_name=name,
        recipe_yaml=_recipe_yaml(name),
        source_msg_ids=[1, 2],
    )


def test_new_pending_defaults():
    suggestion = _suggestion()

    assert suggestion.status == "pending"
    assert suggestion.id
    assert suggestion.created_at == suggestion.updated_at
    assert suggestion.materialized_path is None
    assert suggestion.reject_reason is None


def test_new_pending_normalizes_recipe_name():
    suggestion = RecipeSuggestion.new_pending(
        title="t",
        rationale="r",
        trace_fingerprint="fp",
        recipe_name="News Summary Recipe!",
        recipe_yaml=_recipe_yaml(),
    )

    assert suggestion.recipe_name == "news-summary-recipe"


def test_serialization_roundtrip_preserves_fields():
    suggestion = _suggestion()
    suggestion.cron_hint = "0 8 * * *"

    restored = RecipeSuggestion.from_dict(suggestion.to_dict())

    assert restored.id == suggestion.id
    assert restored.recipe_name == suggestion.recipe_name
    assert restored.recipe_yaml == suggestion.recipe_yaml
    assert restored.cron_hint == "0 8 * * *"
    assert restored.source_msg_ids == [1, 2]
    assert restored.status == "pending"


def test_store_upserts_pending_by_trace_fingerprint(tmp_path):
    store = RecipeSuggestionStore(tmp_path / "recipe_suggestions.jsonl")

    first = store.upsert_pending(_suggestion("fp-same"))
    second = store.upsert_pending(_suggestion("fp-same"))

    assert first.id == second.id
    assert len(store.list_pending()) == 1


def test_store_keeps_distinct_fingerprints_separate(tmp_path):
    store = RecipeSuggestionStore(tmp_path / "recipe_suggestions.jsonl")

    store.upsert_pending(_suggestion("fp-a", "recipe-a"))
    store.upsert_pending(_suggestion("fp-b", "recipe-b"))

    assert len(store.list_pending()) == 2


def test_store_status_transitions(tmp_path):
    store = RecipeSuggestionStore(tmp_path / "recipe_suggestions.jsonl")
    saved = store.upsert_pending(_suggestion())

    accepted = store.update_status(saved.id, "accepted")
    assert accepted is not None and accepted.status == "accepted"

    rejected = store.update_status(saved.id, "rejected", reject_reason="중복 절차")
    assert rejected is not None
    assert rejected.status == "rejected"
    assert rejected.reject_reason == "중복 절차"

    materialized = store.update_status(
        saved.id, "materialized", materialized_path="/tmp/recipes/demo/recipe.yaml"
    )
    assert materialized is not None
    assert materialized.status == "materialized"
    assert materialized.materialized_path == "/tmp/recipes/demo/recipe.yaml"


def test_store_rejects_invalid_status(tmp_path):
    store = RecipeSuggestionStore(tmp_path / "recipe_suggestions.jsonl")
    saved = store.upsert_pending(_suggestion())

    with pytest.raises(ValueError):
        store.update_status(saved.id, "installed")


def test_validate_plan_rejects_unsafe_name():
    errors = validate_recipe_suggestion_plan(
        recipe_name="../evil", recipe_yaml=_recipe_yaml()
    )
    assert any("recipe_name" in e or "Unsafe" in e for e in errors)


def test_validate_plan_rejects_invalid_yaml():
    errors = validate_recipe_suggestion_plan(
        recipe_name="demo-recipe", recipe_yaml="name: [broken"
    )
    assert any("YAML" in e for e in errors)


def test_validate_plan_rejects_steps_based_candidate():
    recipe_yaml = yaml.safe_dump(
        {
            "name": "steps-recipe",
            "instructions": "do it",
            "steps": [{"type": "command", "content": "echo hi"}],
        },
        sort_keys=False,
    )
    errors = validate_recipe_suggestion_plan(
        recipe_name="steps-recipe", recipe_yaml=recipe_yaml
    )
    assert any("steps" in e for e in errors)


def test_validate_plan_rejects_empty_instructions():
    recipe_yaml = yaml.safe_dump({"name": "empty", "instructions": "  "}, sort_keys=False)
    errors = validate_recipe_suggestion_plan(recipe_name="empty", recipe_yaml=recipe_yaml)
    assert any("instructions" in e for e in errors)


def test_validate_plan_rejects_secret_like_content():
    recipe_yaml = _recipe_yaml() + "notes: api_key=abcdef1234567890\n"
    errors = validate_recipe_suggestion_plan(
        recipe_name="demo-recipe", recipe_yaml=recipe_yaml
    )
    assert any("Secret-like" in e for e in errors)


def test_normalize_recipe_name_falls_back_on_invalid_input():
    assert normalize_recipe_name("---") == "recipe-suggestion"
    assert normalize_recipe_name("Daily Report") == "daily-report"


def test_suggestion_from_payload_builds_yaml_and_flags():
    payload = {
        "title": "아침 브리핑",
        "rationale": "매일 반복되는 조회 절차",
        "recipe_name": "morning-briefing",
        "description": "아침 뉴스/날씨 요약",
        "trigger": "아침 브리핑, 모닝 브리핑",
        "instructions": "{{ city }} 날씨와 주요 뉴스를 요약해줘.",
        "required_skills": ["news-search-skill"],
        "parameters": [
            {"name": "city", "description": "도시", "required": False, "default": "서울"}
        ],
        "cron_hint": "0 8 * * *",
        "risk_flags": ["network"],
    }

    suggestion = suggestion_from_recipe_payload(
        payload,
        trace_fingerprint_value="fp-brief",
        source_msg_ids=[10, 11],
        trace=[],
    )

    data = yaml.safe_load(suggestion.recipe_yaml)
    assert data["name"] == "morning-briefing"
    assert data["skills"] == ["news-search-skill"]
    assert data["parameters"][0]["name"] == "city"
    assert suggestion.cron_hint == "0 8 * * *"
    # cron_hint 후보는 승인 화면에서 반복 실행 리스크가 눈에 띄어야 한다.
    assert "cron_hint" in suggestion.risk_flags
    assert "network" in suggestion.risk_flags
    assert suggestion.validation_errors == []
    assert suggestion.status == "pending"


def test_suggestion_from_payload_records_validation_errors():
    payload = {
        "title": "빈 레시피",
        "recipe_name": "empty-recipe",
        "instructions": "   ",
    }

    suggestion = suggestion_from_recipe_payload(
        payload, trace_fingerprint_value="fp-bad", source_msg_ids=[], trace=[]
    )

    assert suggestion.validation_errors


def test_suggestion_from_payload_detects_risks_from_trace():
    """BIZ-435 — LLM risk_flags가 비어 있어도 trace 기반 위험이 합산돼야 한다."""
    trace = [
        SkillTraceStepSnapshot(
            tool_name="web_fetch", arguments={"url": "https://example.com"}
        ),
        SkillTraceStepSnapshot(tool_name="cli", arguments={"command": "ls -al"}),
        SkillTraceStepSnapshot(
            tool_name="file_write", arguments={"path": "out/report.md"}
        ),
    ]
    payload = {
        "title": "리포트 워크플로",
        "recipe_name": "report-workflow",
        "instructions": "{{ query }} 리포트를 만들어줘.",
        "risk_flags": [],
    }

    suggestion = suggestion_from_recipe_payload(
        payload, trace_fingerprint_value="fp-trace", source_msg_ids=[], trace=trace
    )

    assert "network" in suggestion.risk_flags
    assert "subprocess" in suggestion.risk_flags
    assert "file_write" in suggestion.risk_flags
    # 감지 결과도 allowlist 밖 값은 없어야 한다.
    assert set(suggestion.risk_flags) <= set(RECIPE_RISK_FLAG_ALLOWLIST)


def test_detect_trace_risk_flags_finds_external_api_in_arguments():
    trace = [
        SkillTraceStepSnapshot(
            tool_name="mcp_call",
            arguments={"headers": {"authorization": "[REDACTED_SECRET]"}},
        ),
    ]

    assert "external_api" in detect_trace_risk_flags(trace)


def test_detect_trace_risk_flags_ignores_benign_trace():
    trace = [
        SkillTraceStepSnapshot(tool_name="search_memory", arguments={"q": "메모"}),
        SkillTraceStepSnapshot(tool_name="clarify", arguments={"question": "언제?"}),
    ]

    assert detect_trace_risk_flags(trace) == []


def test_suggestion_from_payload_drops_non_allowlisted_flags(caplog):
    """BIZ-435 — unknown/secret-like risk flag는 저장되지 않고 로그에도 값이 안 남는다."""
    payload = {
        "title": "정화 대상",
        "recipe_name": "sanitize-me",
        "instructions": "{{ query }}를 확인해줘.",
        "risk_flags": ["network", "totally-unknown-flag", "token=SECRET123VALUE"],
    }

    with caplog.at_level("WARNING"):
        suggestion = suggestion_from_recipe_payload(
            payload, trace_fingerprint_value="fp-clean", source_msg_ids=[], trace=[]
        )

    assert "network" in suggestion.risk_flags
    assert set(suggestion.risk_flags) <= set(RECIPE_RISK_FLAG_ALLOWLIST)
    # drop 된 값 자체(잠재적 secret)는 어떤 로그에도 남지 않는다.
    assert "SECRET123VALUE" not in caplog.text
    assert "totally-unknown-flag" not in caplog.text


def test_new_pending_sanitizes_risk_flags():
    suggestion = RecipeSuggestion.new_pending(
        title="t",
        rationale="r",
        trace_fingerprint="fp",
        recipe_name="demo-recipe",
        recipe_yaml=_recipe_yaml(),
        risk_flags=["Network", "cron_hint", "api_key=abcd1234efgh5678"],
    )

    assert suggestion.risk_flags == ["cron_hint", "network"]


def test_from_dict_sanitizes_legacy_risk_flags():
    """BIZ-435 — legacy 저장분의 임의 문자열 risk flag는 로드 시점에 정화된다."""
    raw = _suggestion().to_dict()
    raw["risk_flags"] = ["network", "cron_hint", "ghp_abcdefghijklmnopqrstuv", "??"]

    restored = RecipeSuggestion.from_dict(raw)

    assert restored.risk_flags == ["cron_hint", "network"]


def test_normalize_recipe_risk_flags_accepts_cron_hint():
    assert normalize_recipe_risk_flags(["cron_hint", "junk"]) == ["cron_hint"]
    assert normalize_recipe_risk_flags("not-a-list") == []


def test_build_recipe_candidate_prompt_focuses_on_recipe_semantics():
    prompt = build_recipe_candidate_prompt(user_text="u", assistant_text="a", trace=[])

    lowered = prompt.lower()
    assert "recipe" in lowered
    assert "not a new skill" in lowered
    assert "parameters" in lowered
    assert "cron_hint" in prompt
    assert "do not include secrets" in lowered


def test_response_schema_requires_all_fields_and_ordering():
    props = set(RECIPE_SUGGESTION_RESPONSE_SCHEMA["properties"])
    required = set(RECIPE_SUGGESTION_RESPONSE_SCHEMA["required"])
    ordering = RECIPE_SUGGESTION_RESPONSE_SCHEMA["propertyOrdering"]

    assert props == required == set(ordering)
    assert "recipe_name" in props
    assert "cron_hint" in props


def test_response_schema_blocks_unknown_properties_and_enforces_flag_enum():
    """BIZ-435 — root/parameter item에서 unknown property를 차단하고 risk_flags를
    allowlist enum으로 제한한다."""
    assert RECIPE_SUGGESTION_RESPONSE_SCHEMA["additionalProperties"] is False

    parameter_item = RECIPE_SUGGESTION_RESPONSE_SCHEMA["properties"]["parameters"][
        "items"
    ]
    assert parameter_item["additionalProperties"] is False

    flag_items = RECIPE_SUGGESTION_RESPONSE_SCHEMA["properties"]["risk_flags"]["items"]
    # LLM self-declare는 skill 공용 allowlist만 허용 — cron_hint는 내부 파생값.
    assert set(flag_items["enum"]) == set(RISK_FLAG_ALLOWLIST)
    assert "cron_hint" not in flag_items["enum"]
