"""BIZ-490 — compact Planner capability catalog contract tests."""

from __future__ import annotations

import json

import pytest

from simpleclaw.agent import planner_catalog as planner_catalog_module
from simpleclaw.agent.planner_catalog import (
    DESCRIPTION_MAX_CHARS,
    PlannerAsset,
    PlannerCatalogSensitiveTextError,
    build_planner_catalog,
    catalog_prompt_metrics,
)
from simpleclaw.agent.tool_schemas import (
    NativeToolSpec,
    ToolRisk,
    ToolScope,
)
from simpleclaw.capability import CapabilityMetadata
from simpleclaw.evaluation.turn_planner_eval import aggregate_results
from simpleclaw.llm.models import ToolDefinition
from simpleclaw.recipes.models import RecipeDefinition
from simpleclaw.skills.models import SkillDefinition


def _tool(
    name: str,
    *,
    description: str = "Native lookup.",
    scope: ToolScope = ToolScope.RUNTIME,
    risk: ToolRisk = ToolRisk.LOW,
    operator_gate_required: bool = False,
) -> NativeToolSpec:
    return NativeToolSpec(
        ToolDefinition(
            name=name,
            description=description,
            parameters={"type": "object", "properties": {}, "required": []},
        ),
        scope=scope,
        risk=risk,
        operator_gate_required=operator_gate_required,
    )


def _declared_capability() -> CapabilityMetadata:
    return CapabilityMetadata(
        domains=("market",),
        intents=("quote", "history"),
        read_only=True,
        side_effects=False,
        freshness_sensitive=True,
        direct_answer=True,
        output_contract="structured_evidence",
        declared=True,
    )


def test_skill_recipe_and_native_tool_share_compact_shape():
    skill = SkillDefinition(
        name="kr-stock-skill",
        description="Korean stock quotes and history",
        script_path="/private/secret/run.py",
        skill_dir="/private/secret",
        commands=["python /private/secret/run.py"],
        capability=_declared_capability(),
    )
    recipe = RecipeDefinition(
        name="market-close",
        description="Summarize the market close.",
        instructions="Read /private/secret/config.yaml",
        recipe_dir="/private/secret/recipe",
        capability=_declared_capability(),
    )

    catalog = build_planner_catalog(
        skills=[skill],
        recipes=[recipe],
        native_specs=[_tool("web_search")],
    )
    payload = json.loads(catalog.to_prompt_json())
    assets = {(item["type"], item["name"]): item for item in payload}

    assert set(assets) == {
        ("native_tool", "web_search"),
        ("recipe", "market-close"),
        ("skill", "kr-stock-skill"),
    }
    assert assets[("skill", "kr-stock-skill")] == {
        "type": "skill",
        "name": "kr-stock-skill",
        "description": "Korean stock quotes and history",
        "domains": ["market"],
        "intents": ["history", "quote"],
        "read_only": True,
        "side_effects": False,
        "freshness_sensitive": True,
        "direct_answer": True,
        "requires_confirmation": False,
        "output_contract": "structured_evidence",
        "declared": True,
    }
    serialized = catalog.to_prompt_json()
    assert "/private/secret" not in serialized
    assert "run.py" not in serialized
    assert "config.yaml" not in serialized
    assert "parameters" not in serialized


def test_undeclared_assets_keep_conservative_capability_defaults():
    skill = SkillDefinition(name="legacy", description="Legacy skill")
    recipe = RecipeDefinition(name="legacy-recipe", description="Legacy recipe")

    catalog = build_planner_catalog(skills=[skill], recipes=[recipe], native_specs=[])

    for asset in catalog.assets:
        assert asset.declared is False
        assert asset.read_only is False
        assert asset.side_effects is True
        assert asset.requires_confirmation is False


def test_runtime_prompt_hides_internal_native_tools_but_snapshot_keeps_visibility():
    catalog = build_planner_catalog(
        native_specs=[
            _tool("web_search"),
            _tool(
                "asset_inventory",
                scope=ToolScope.OPERATOR,
                operator_gate_required=True,
            ),
            _tool(
                "recipe_generate",
                scope=ToolScope.DEVELOPMENT,
                risk=ToolRisk.HIGH,
                operator_gate_required=True,
            ),
        ]
    )

    by_name = {asset.name: asset for asset in catalog.assets}
    assert by_name["web_search"].runtime_visible is True
    assert by_name["asset_inventory"].runtime_visible is False
    assert by_name["recipe_generate"].runtime_visible is False
    assert [item["name"] for item in json.loads(catalog.to_prompt_json())] == [
        "web_search"
    ]
    assert {item["name"] for item in json.loads(catalog.to_prompt_json(runtime_only=False))} == {
        "asset_inventory",
        "recipe_generate",
        "web_search",
    }


def test_native_scope_and_risk_are_mapped_conservatively():
    catalog = build_planner_catalog(
        native_specs=[
            _tool("web_search"),
            _tool("file_write", risk=ToolRisk.MEDIUM),
            _tool(
                "restart_runtime",
                scope=ToolScope.OPERATOR,
                risk=ToolRisk.HIGH,
                operator_gate_required=True,
            ),
        ]
    )
    by_name = {asset.name: asset for asset in catalog.assets}

    assert by_name["web_search"].read_only is True
    assert by_name["web_search"].side_effects is False
    assert by_name["file_write"].read_only is False
    assert by_name["file_write"].side_effects is True
    assert by_name["restart_runtime"].requires_confirmation is True


def test_sort_and_fingerprint_are_stable_across_input_order():
    assets = [
        SkillDefinition(name="zeta", description="Z"),
        SkillDefinition(name="alpha", description="A"),
    ]
    recipes = [
        RecipeDefinition(name="two", description="2"),
        RecipeDefinition(name="one", description="1"),
    ]
    tools = [_tool("web_search"), _tool("file_read")]

    first = build_planner_catalog(
        skills=assets,
        recipes=recipes,
        native_specs=tools,
    )
    second = build_planner_catalog(
        skills=reversed(assets),
        recipes=reversed(recipes),
        native_specs=reversed(tools),
    )

    assert first.assets == second.assets
    assert first.fingerprint == second.fingerprint
    assert first.to_prompt_json() == second.to_prompt_json()
    assert len(first.fingerprint) == 64


def test_description_is_whitespace_normalized_and_clamped():
    description = "  first\n\nline  " + ("x" * (DESCRIPTION_MAX_CHARS + 40))
    catalog = build_planner_catalog(
        native_specs=[_tool("long", description=description)]
    )

    compact = catalog.assets[0].description
    assert "\n" not in compact
    assert "  " not in compact
    assert len(compact) == DESCRIPTION_MAX_CHARS
    assert compact.endswith("…")


def test_description_allows_non_path_slash_compounds():
    description = "실적 리뷰/프리뷰 — 발표 수치와 주가 반응을 분리"

    catalog = build_planner_catalog(
        recipes=[
            RecipeDefinition(
                name="earnings",
                description=description,
            )
        ],
        native_specs=[],
    )

    assert catalog.assets[0].description == description


@pytest.mark.parametrize(
    ("asset_type", "sensitive_text", "expected_reason"),
    [
        (
            "skill",
            "Read " + "/" + "private/example/config.yaml before use.",
            "absolute_path",
        ),
        (
            "recipe",
            "Use " + "API_" + "KEY=synthetic-only for the fixture.",
            "credential",
        ),
        (
            "native_tool",
            "Read " + "/" + "tmp/synthetic-catalog-input.",
            "absolute_path",
        ),
    ],
)
def test_sensitive_description_fails_before_fingerprint_or_prompt(
    asset_type,
    sensitive_text,
    expected_reason,
    monkeypatch,
    caplog,
):
    def unexpected_fingerprint(*_args, **_kwargs):
        raise AssertionError("fingerprint must not run for sensitive catalog text")

    monkeypatch.setattr(
        planner_catalog_module.hashlib,
        "sha256",
        unexpected_fingerprint,
    )
    kwargs = {
        "skills": [
            SkillDefinition(
                name="sensitive-skill",
                description=sensitive_text,
            )
        ]
        if asset_type == "skill"
        else [],
        "recipes": [
            RecipeDefinition(
                name="sensitive-recipe",
                description=sensitive_text,
            )
        ]
        if asset_type == "recipe"
        else [],
        "native_specs": (
            [_tool("sensitive_tool", description=sensitive_text)]
            if asset_type == "native_tool"
            else []
        ),
    }

    with pytest.raises(PlannerCatalogSensitiveTextError) as exc_info:
        build_planner_catalog(**kwargs)

    error = str(exc_info.value)
    assert exc_info.value.reason == expected_reason
    assert exc_info.value.code == (
        f"planner_catalog_sensitive_text.{expected_reason}"
    )
    assert "field=description" in error
    assert sensitive_text not in error
    assert sensitive_text not in caplog.text


def test_sensitive_output_contract_fails_closed_without_raw_value():
    synthetic_token = "sk-" + "synthetic-catalog-token-123456"
    capability = CapabilityMetadata(
        output_contract=synthetic_token,
        declared=True,
    )

    with pytest.raises(PlannerCatalogSensitiveTextError) as exc_info:
        build_planner_catalog(
            skills=[
                SkillDefinition(
                    name="contract-skill",
                    description="Safe description.",
                    capability=capability,
                )
            ],
            native_specs=[],
        )

    assert exc_info.value.code == "planner_catalog_sensitive_text.credential"
    assert "asset=skill/contract-skill" in str(exc_info.value)
    assert "field=output_contract" in str(exc_info.value)
    assert synthetic_token not in str(exc_info.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "/" + "private/example/name"),
        ("domains", ("safe", "/" + "private/example/domain")),
        ("intents", ("safe", "TOKEN=" + "synthetic-only")),
    ],
)
def test_planner_asset_rejects_sensitive_text_in_every_serialized_field(
    field,
    value,
):
    kwargs = {
        "asset_type": "skill",
        "name": "safe-name",
        "description": "Safe description.",
        "domains": ("safe",),
        "intents": ("safe",),
        "read_only": False,
        "side_effects": True,
        "freshness_sensitive": False,
        "direct_answer": False,
        "requires_confirmation": False,
        "output_contract": None,
        "declared": False,
        "runtime_visible": True,
    }
    kwargs[field] = value

    with pytest.raises(PlannerCatalogSensitiveTextError) as exc_info:
        PlannerAsset(**kwargs)

    assert f"field={field.rstrip('s')}" in str(exc_info.value)
    assert str(value) not in str(exc_info.value)


def test_sensitive_failure_is_deterministic_and_credential_free():
    sensitive_text = "PASSWORD=" + "synthetic-only"
    errors = []

    for _ in range(2):
        with pytest.raises(PlannerCatalogSensitiveTextError) as exc_info:
            build_planner_catalog(
                recipes=[
                    RecipeDefinition(
                        name="deterministic-recipe",
                        description=sensitive_text,
                    )
                ],
                native_specs=[],
            )
        errors.append(str(exc_info.value))

    assert errors == [
        (
            "planner_catalog_sensitive_text.credential: "
            "asset=recipe/deterministic-recipe field=description"
        ),
    ] * 2
    assert sensitive_text not in errors[0]


def test_51_asset_prompt_report_stays_below_prototype_token_budget():
    skills = [
        SkillDefinition(
            name=f"skill-{index:02d}",
            description=f"Compact capability {index}",
            capability=_declared_capability(),
        )
        for index in range(51)
    ]
    catalog = build_planner_catalog(skills=skills, native_specs=[])

    metrics = catalog_prompt_metrics(catalog)

    assert metrics["asset_count"] == 51
    assert metrics["character_count"] == len(catalog.to_prompt_json())
    assert metrics["estimated_tokens"] < 3850

    report = aggregate_results(
        [],
        variant="off",
        repeat=1,
        baseline="catalog-size",
        catalog_metrics=metrics,
    )
    assert report["summary"]["catalog_payload"] == metrics


def test_evaluator_catalog_metrics_reject_non_numeric_payload():
    try:
        aggregate_results(
            [],
            variant="off",
            repeat=1,
            baseline="catalog-size",
            catalog_metrics={
                "asset_count": 1,
                "character_count": 100,
                "estimated_tokens": 25,
                "path": "/private/secret",  # type: ignore[dict-item]
            },
        )
    except ValueError as exc:
        assert "catalog_metrics" in str(exc)
    else:
        raise AssertionError("unexpected catalog metric fields must fail closed")


def test_duplicate_type_and_name_are_rejected():
    duplicate = SkillDefinition(name="same", description="one")

    try:
        build_planner_catalog(
            skills=[duplicate, SkillDefinition(name="same", description="two")],
            native_specs=[],
        )
    except ValueError as exc:
        assert "duplicate planner asset" in str(exc)
    else:
        raise AssertionError("duplicate planner asset must fail closed")


def test_planner_asset_rejects_unknown_type():
    try:
        PlannerAsset(
            asset_type="mcp",
            name="unknown",
            description="",
            domains=(),
            intents=(),
            read_only=False,
            side_effects=True,
            freshness_sensitive=False,
            direct_answer=False,
            requires_confirmation=False,
            output_contract=None,
            declared=False,
            runtime_visible=False,
        )
    except ValueError as exc:
        assert "asset_type" in str(exc)
    else:
        raise AssertionError("unknown asset type must fail closed")
