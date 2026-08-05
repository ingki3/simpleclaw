"""Actual-provider V4 evaluator assembly regression tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from simpleclaw.agent.tool_schemas import NativeToolSpec


SCRIPT = (
    Path(__file__).parents[2] / "scripts" / "dev" / "evaluate_langgraph_v4_scenarios.py"
)


def _load_evaluator_script():
    spec = importlib.util.spec_from_file_location(
        "evaluate_langgraph_v4_scenarios_biz580", SCRIPT
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_catalog_passes_native_specs_to_connected_probe_definitions(
    tmp_path: Path,
) -> None:
    recipes_dir = tmp_path / "recipes"
    local_skills_dir = tmp_path / "local-skills"
    global_skills_dir = tmp_path / "global-skills"
    for directory in (recipes_dir, local_skills_dir, global_skills_dir):
        directory.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            (
                "recipes:",
                f"  dir: {recipes_dir}",
                "skills:",
                f"  local_dir: {local_skills_dir}",
                f"  global_dir: {global_skills_dir}",
                "",
            )
        ),
        encoding="utf-8",
    )

    module = _load_evaluator_script()
    catalog, definitions, _recipe_names = module._runtime_catalog(config)

    catalog_native_names = {
        asset.name for asset in catalog.assets if asset.asset_type == "native_tool"
    }
    definition_native_names = {
        definition.name
        for definition in definitions
        if isinstance(definition, NativeToolSpec)
    }
    assert {"web_fetch", "web_search"} <= catalog_native_names
    assert definition_native_names == catalog_native_names
