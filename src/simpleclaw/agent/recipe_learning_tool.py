"""operator-only recipe_learning native tool handler.

성공한 복잡 tool trace에서 생성된 pending :class:`RecipeSuggestion` 후보를
list/show/accept/reject/materialize 한다 (BIZ-428).

설계 결정:
- materialize는 live ``recipes.dir`` 를 쓰는 유일한 경로이며,
  ``recipe_generate`` install과 동일한 policy(:func:`install_validated_recipe`)를
  재사용한다 — 승인 상태 + ``confirm=true`` + validation pass + overwrite/backup.
- ``require_operator_accept`` (기본 true)면 ``accepted`` 상태 후보만 materialize
  가능하다. false로 낮추면 pending 후보도 confirm과 함께 설치할 수 있다.
- materialize 직전에 정적 검증(:func:`validate_recipe_suggestion_plan`)과
  loader/render smoke(:func:`validate_recipe_candidate`)를 다시 수행한다 —
  후보 저장 이후 코드/정책이 바뀌었어도 설치 시점 기준으로 안전해야 한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from simpleclaw.agent.recipe_generate import (
    install_validated_recipe,
    validate_recipe_candidate,
)
from simpleclaw.config import load_recipes_config
from simpleclaw.recipes.learning import (
    RecipeSuggestion,
    RecipeSuggestionStore,
    validate_recipe_suggestion_plan,
)

_DEFAULT_SUGGESTIONS_FILE = "~/.simpleclaw-agent/default/recipe_suggestions.jsonl"


def handle_recipe_learning(
    args: dict[str, Any],
    *,
    config: dict,
    config_path: str | Path,
    workspace_dir: str | Path,
) -> str:
    """pending recipe 후보를 조회/승인/거절/materialize 한다."""
    action = str(args.get("action") or "list")
    store = RecipeSuggestionStore(
        config.get("suggestions_file", _DEFAULT_SUGGESTIONS_FILE)
    )
    if action == "list":
        status = str(args.get("status") or "pending")
        items = store.list_pending() if status == "pending" else store.list_all()
        return json.dumps(
            [_summary(s) for s in items], ensure_ascii=False, indent=2
        )
    suggestion_id = str(args.get("id") or "")
    if not suggestion_id:
        return "Error: 'id' is required for this action."
    suggestion = store.get(suggestion_id)
    if suggestion is None:
        return f"Error: recipe suggestion not found: {suggestion_id}"
    if action == "show":
        return json.dumps(suggestion.to_dict(), ensure_ascii=False, indent=2)
    if action == "accept":
        updated = store.update_status(suggestion_id, "accepted")
        return json.dumps(
            updated.to_dict() if updated else {}, ensure_ascii=False, indent=2
        )
    if action == "reject":
        updated = store.update_status(
            suggestion_id, "rejected", reject_reason=str(args.get("reason") or "")
        )
        return json.dumps(
            updated.to_dict() if updated else {}, ensure_ascii=False, indent=2
        )
    if action == "materialize":
        return _materialize(
            args,
            suggestion=suggestion,
            store=store,
            config=config,
            config_path=config_path,
            workspace_dir=workspace_dir,
        )
    return f"Error: unknown recipe_learning action '{action}'."


def _materialize(
    args: dict[str, Any],
    *,
    suggestion: RecipeSuggestion,
    store: RecipeSuggestionStore,
    config: dict,
    config_path: str | Path,
    workspace_dir: str | Path,
) -> str:
    """승인된 후보를 configured ``recipes.dir`` 에 검증/백업 정책 아래 설치한다."""
    recipes_dir = Path(load_recipes_config(Path(config_path))["dir"]).expanduser()
    target_path = recipes_dir / suggestion.recipe_name / "recipe.yaml"
    payload: dict[str, Any] = {
        "ok": False,
        "action": "materialize",
        "id": suggestion.id,
        "recipe_name": suggestion.recipe_name,
        "status": suggestion.status,
        "target_path": str(target_path),
        "installed": False,
        "backup_path": None,
        "errors": [],
        "warnings": [],
    }

    # 승인 게이트 — 자동 materialize 금지. require_operator_accept=false 로
    # 명시적으로 낮춘 운영 환경에서만 pending 후보 직접 설치를 허용한다.
    allowed_statuses = (
        ("accepted",)
        if bool(config.get("require_operator_accept", True))
        else ("pending", "accepted")
    )
    if suggestion.status not in allowed_statuses:
        payload["errors"].append(
            f"materialize requires status in {list(allowed_statuses)}; "
            f"current status is '{suggestion.status}'"
        )
        return _json(payload)
    if not bool(args.get("confirm", False)):
        payload["errors"].append("materialize requires confirm=true")
        return _json(payload)

    # 정적 재검증 — 후보 저장 시점의 validation_errors 를 신뢰하지 않고 설치
    # 시점에 name/YAML/secret/steps 게이트를 다시 통과해야 한다.
    static_errors = validate_recipe_suggestion_plan(
        recipe_name=suggestion.recipe_name, recipe_yaml=suggestion.recipe_yaml
    )
    if static_errors:
        payload["errors"].extend(static_errors)
        return _json(payload)

    candidate_args = _candidate_args_from_yaml(suggestion)
    validation = validate_recipe_candidate(
        candidate_args,
        candidate_dir=Path(workspace_dir).expanduser()
        / "recipe_learning_drafts"
        / suggestion.recipe_name,
    )
    payload["validation"] = validation
    payload["warnings"].extend(validation.get("warnings", []))
    if not validation["ok"]:
        payload["errors"].extend(validation.get("errors", []))
        return _json(payload)

    install = install_validated_recipe(
        {
            **candidate_args,
            "confirm": bool(args.get("confirm", False)),
            "overwrite": bool(args.get("overwrite", False)),
        },
        target_path=target_path,
    )
    payload["errors"].extend(install["errors"])
    payload.update(
        {
            "ok": install["ok"],
            "installed": install["installed"],
            "backup_path": install["backup_path"],
        }
    )
    if install["recipe"] is not None:
        payload["recipe"] = install["recipe"]
    if install["installed"]:
        updated = store.update_status(
            suggestion.id, "materialized", materialized_path=str(target_path)
        )
        payload["suggestion"] = updated.to_dict() if updated else None
    return _json(payload)


def _candidate_args_from_yaml(suggestion: RecipeSuggestion) -> dict[str, Any]:
    """저장된 recipe_yaml을 recipe_generate 계열 helper의 args shape로 되돌린다."""
    data = yaml.safe_load(suggestion.recipe_yaml or "")
    data = data if isinstance(data, dict) else {}
    return {
        "name": suggestion.recipe_name,
        "description": str(data.get("description") or ""),
        "trigger": str(data.get("trigger") or ""),
        "instructions": str(data.get("instructions") or ""),
        "skills": data.get("skills") or [],
        "parameters": data.get("parameters") or [],
    }


def _summary(suggestion: RecipeSuggestion) -> dict[str, Any]:
    """list 응답용 후보 요약 — 승인 판단에 필요한 리스크/오류를 함께 노출한다."""
    return {
        "id": suggestion.id,
        "title": suggestion.title,
        "recipe_name": suggestion.recipe_name,
        "status": suggestion.status,
        "cron_hint": suggestion.cron_hint,
        "risk_flags": suggestion.risk_flags,
        "validation_errors": suggestion.validation_errors,
        "updated_at": suggestion.updated_at.isoformat(),
    }


def _json(payload: dict[str, Any]) -> str:
    """Tool result JSON을 안정적인 key order로 직렬화한다."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


__all__ = ["handle_recipe_learning"]
