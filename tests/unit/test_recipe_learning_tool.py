"""recipe_learning 운영자 native tool 단위 테스트 (BIZ-428)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.agent.recipe_learning_tool import handle_recipe_learning
from simpleclaw.agent.tool_schemas import ToolScope, build_tool_definitions
from simpleclaw.llm.models import ToolCall
from simpleclaw.recipes.learning import RecipeSuggestion, RecipeSuggestionStore
from simpleclaw.skills.learning import SkillTraceStepSnapshot


def _write_config(tmp_path, recipes_dir):
    config = tmp_path / "config.yaml"
    config.write_text(f"recipes:\n  dir: {recipes_dir}\n", encoding="utf-8")
    return config


def _recipe_yaml(name: str = "demo-recipe") -> str:
    return yaml.safe_dump(
        {
            "name": name,
            "description": "데모 레시피",
            "parameters": [
                {"name": "query", "description": "질의", "required": False, "default": "롯데"}
            ],
            "instructions": "{{ query }} 상황을 확인해줘.\n",
        },
        allow_unicode=True,
        sort_keys=False,
    )


def _seed_suggestion(store: RecipeSuggestionStore, name: str = "demo-recipe"):
    return store.upsert_pending(
        RecipeSuggestion.new_pending(
            title="데모 워크플로",
            rationale="반복 조회 절차",
            trace_fingerprint=f"fp-{name}",
            recipe_name=name,
            recipe_yaml=_recipe_yaml(name),
            risk_flags=["network"],
        )
    )


def _tool_env(tmp_path):
    """handler 호출에 필요한 store/config/경로 묶음을 준비한다."""
    recipes_dir = tmp_path / "recipes"
    config_path = _write_config(tmp_path, recipes_dir)
    suggestions_file = tmp_path / "recipe_suggestions.jsonl"
    store = RecipeSuggestionStore(suggestions_file)
    config = {"suggestions_file": str(suggestions_file), "require_operator_accept": True}
    kwargs = {
        "config": config,
        "config_path": config_path,
        "workspace_dir": tmp_path / "workspace",
    }
    return store, kwargs, recipes_dir


def test_recipe_learning_is_not_exposed_to_runtime_context():
    runtime_names = {tool.name for tool in build_tool_definitions(skills=[])}
    operator_names = {
        tool.name
        for tool in build_tool_definitions(
            skills=[],
            scopes=(ToolScope.RUNTIME, ToolScope.OPERATOR, ToolScope.DEVELOPMENT),
            operator_gate=True,
        )
    }

    assert "recipe_learning" not in runtime_names
    assert "recipe_learning" in operator_names


def test_list_returns_pending_summaries(tmp_path):
    store, kwargs, _ = _tool_env(tmp_path)
    saved = _seed_suggestion(store)

    result = json.loads(handle_recipe_learning({"action": "list"}, **kwargs))

    assert len(result) == 1
    summary = result[0]
    assert summary["id"] == saved.id
    assert summary["recipe_name"] == "demo-recipe"
    assert summary["status"] == "pending"
    assert summary["risk_flags"] == ["network"]
    assert "validation_errors" in summary


def test_show_returns_yaml_flags_errors_and_trace(tmp_path):
    store, kwargs, _ = _tool_env(tmp_path)
    saved = _seed_suggestion(store)

    result = json.loads(
        handle_recipe_learning({"action": "show", "id": saved.id}, **kwargs)
    )

    assert result["recipe_yaml"] == saved.recipe_yaml
    assert result["risk_flags"] == ["network"]
    assert result["validation_errors"] == []
    assert "trace" in result


def test_list_and_show_sanitize_legacy_risk_flags(tmp_path):
    """BIZ-435 — legacy 저장분의 unknown/secret-like risk flag는 list/show 어디에도
    노출되지 않고, trace/validation_errors 요약은 유지된다."""
    store, kwargs, _ = _tool_env(tmp_path)
    raw = RecipeSuggestion.new_pending(
        title="레거시 후보",
        rationale="r",
        trace_fingerprint="fp-legacy",
        recipe_name="legacy-recipe",
        recipe_yaml=_recipe_yaml("legacy-recipe"),
        trace=[
            SkillTraceStepSnapshot(
                tool_name="web_fetch", arguments={"url": "https://example.com"}
            )
        ],
        validation_errors=["recipe candidate must include non-empty instructions (v1)"],
    ).to_dict()
    raw["risk_flags"] = ["network", "token=abcd1234efgh5678", "weird-flag"]
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps(raw, ensure_ascii=False) + "\n", encoding="utf-8")

    listed = handle_recipe_learning({"action": "list"}, **kwargs)
    shown = handle_recipe_learning({"action": "show", "id": raw["id"]}, **kwargs)

    assert json.loads(listed)[0]["risk_flags"] == ["network"]
    assert json.loads(listed)[0]["validation_errors"]
    shown_data = json.loads(shown)
    assert shown_data["risk_flags"] == ["network"]
    assert shown_data["trace"][0]["tool_name"] == "web_fetch"
    for output in (listed, shown):
        assert "abcd1234efgh5678" not in output
        assert "weird-flag" not in output


def test_accept_and_reject_update_status(tmp_path):
    store, kwargs, _ = _tool_env(tmp_path)
    first = _seed_suggestion(store, "recipe-a")
    second = _seed_suggestion(store, "recipe-b")

    accepted = json.loads(
        handle_recipe_learning({"action": "accept", "id": first.id}, **kwargs)
    )
    rejected = json.loads(
        handle_recipe_learning(
            {"action": "reject", "id": second.id, "reason": "중복 절차"}, **kwargs
        )
    )

    assert accepted["status"] == "accepted"
    assert rejected["status"] == "rejected"
    assert rejected["reject_reason"] == "중복 절차"
    assert store.get(first.id).status == "accepted"
    assert store.get(second.id).status == "rejected"


def test_unknown_id_and_action_return_errors(tmp_path):
    _, kwargs, _ = _tool_env(tmp_path)

    missing = handle_recipe_learning({"action": "show", "id": "nope"}, **kwargs)
    no_id = handle_recipe_learning({"action": "accept"}, **kwargs)
    unknown = handle_recipe_learning({"action": "install", "id": "x"}, **kwargs)

    assert missing.startswith("Error:")
    assert no_id.startswith("Error:")
    assert "not found" in unknown or unknown.startswith("Error:")


def test_materialize_requires_accepted_status(tmp_path):
    store, kwargs, recipes_dir = _tool_env(tmp_path)
    saved = _seed_suggestion(store)

    result = json.loads(
        handle_recipe_learning(
            {"action": "materialize", "id": saved.id, "confirm": True}, **kwargs
        )
    )

    assert result["ok"] is False
    assert any("accepted" in e for e in result["errors"])
    assert not (recipes_dir / "demo-recipe" / "recipe.yaml").exists()


def test_materialize_requires_confirm(tmp_path):
    store, kwargs, recipes_dir = _tool_env(tmp_path)
    saved = _seed_suggestion(store)
    store.update_status(saved.id, "accepted")

    result = json.loads(
        handle_recipe_learning({"action": "materialize", "id": saved.id}, **kwargs)
    )

    assert result["ok"] is False
    assert any("confirm=true" in e for e in result["errors"])
    assert not (recipes_dir / "demo-recipe" / "recipe.yaml").exists()


def test_materialize_installs_accepted_suggestion(tmp_path):
    store, kwargs, recipes_dir = _tool_env(tmp_path)
    saved = _seed_suggestion(store)
    store.update_status(saved.id, "accepted")

    result = json.loads(
        handle_recipe_learning(
            {"action": "materialize", "id": saved.id, "confirm": True}, **kwargs
        )
    )

    target = recipes_dir / "demo-recipe" / "recipe.yaml"
    assert result["ok"] is True
    assert result["installed"] is True
    assert target.is_file()
    assert yaml.safe_load(target.read_text(encoding="utf-8"))["name"] == "demo-recipe"
    assert store.get(saved.id).status == "materialized"
    assert store.get(saved.id).materialized_path == str(target)


def test_materialize_collision_requires_overwrite_and_creates_backup(tmp_path):
    store, kwargs, recipes_dir = _tool_env(tmp_path)
    saved = _seed_suggestion(store)
    store.update_status(saved.id, "accepted")
    target_dir = recipes_dir / "demo-recipe"
    target_dir.mkdir(parents=True)
    (target_dir / "recipe.yaml").write_text(
        "name: demo-recipe\ninstructions: old\n", encoding="utf-8"
    )

    blocked = json.loads(
        handle_recipe_learning(
            {"action": "materialize", "id": saved.id, "confirm": True}, **kwargs
        )
    )
    assert blocked["ok"] is False
    assert any("overwrite=true" in e for e in blocked["errors"])
    assert store.get(saved.id).status == "accepted"

    installed = json.loads(
        handle_recipe_learning(
            {
                "action": "materialize",
                "id": saved.id,
                "confirm": True,
                "overwrite": True,
            },
            **kwargs,
        )
    )

    assert installed["ok"] is True
    assert installed["backup_path"]
    assert Path(installed["backup_path"]).is_file()
    assert "old" in Path(installed["backup_path"]).read_text(encoding="utf-8")
    assert store.get(saved.id).status == "materialized"


def test_materialize_rejects_candidate_with_static_errors(tmp_path):
    store, kwargs, recipes_dir = _tool_env(tmp_path)
    saved = store.upsert_pending(
        RecipeSuggestion.new_pending(
            title="비밀 포함 후보",
            rationale="r",
            trace_fingerprint="fp-secret",
            recipe_name="secret-recipe",
            recipe_yaml=(
                "name: secret-recipe\n"
                "instructions: use api_key=abcdef1234567890\n"
            ),
        )
    )
    store.update_status(saved.id, "accepted")

    result = json.loads(
        handle_recipe_learning(
            {"action": "materialize", "id": saved.id, "confirm": True}, **kwargs
        )
    )

    assert result["ok"] is False
    assert any("Secret-like" in e for e in result["errors"])
    assert not (recipes_dir / "secret-recipe" / "recipe.yaml").exists()


def test_materialize_rejects_pending_even_when_accept_config_disabled(tmp_path):
    """require_operator_accept=False 설정으로도 accepted 게이트는 우회되지 않는다 (BIZ-435)."""
    store, kwargs, recipes_dir = _tool_env(tmp_path)
    kwargs["config"]["require_operator_accept"] = False
    saved = _seed_suggestion(store)

    result = json.loads(
        handle_recipe_learning(
            {"action": "materialize", "id": saved.id, "confirm": True}, **kwargs
        )
    )

    assert result["ok"] is False
    assert any("accepted" in e for e in result["errors"])
    assert not (recipes_dir / "demo-recipe" / "recipe.yaml").exists()
    assert store.get(saved.id).status == "pending"


@pytest.mark.asyncio
async def test_orchestrator_recipe_learning_dispatch_requires_operator_context(
    tmp_path, monkeypatch
):
    """수동 dispatch도 operator context가 아니면 recipe_learning을 실행하지 않는다."""
    config = _write_config(tmp_path, tmp_path / "recipes")
    monkeypatch.setattr("simpleclaw.agent.orchestrator.create_router", MagicMock())
    monkeypatch.setattr(
        "simpleclaw.agent.tool_dispatch.handle_recipe_learning",
        lambda args, **kwargs: json.dumps([]),
    )
    orchestrator = AgentOrchestrator(config)
    tool_call = ToolCall(
        id="recipe-learning-1", name="recipe_learning", arguments={"action": "list"}
    )

    blocked = await orchestrator._dispatch_tool_call(tool_call)
    allowed = await orchestrator._dispatch_tool_call(tool_call, operator_tools=True)

    assert "operator context" in blocked
    assert json.loads(allowed) == []
