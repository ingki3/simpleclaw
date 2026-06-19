"""operator-only skill_learning native tool handler."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from simpleclaw.skills.learning import SkillSuggestionStore
from simpleclaw.skills.materializer import materialize_skill_suggestion


def handle_skill_learning(
    args: dict[str, Any], *, config: dict, skills_config: dict
) -> str:
    """pending skill 후보를 조회/승인/거절/materialize 한다."""
    action = str(args.get("action") or "list")
    store = SkillSuggestionStore(
        config.get(
            "suggestions_file", "~/.simpleclaw-agent/default/skill_suggestions.jsonl"
        )
    )
    if action == "list":
        status = str(args.get("status") or "pending")
        items = store.list_pending() if status == "pending" else store.list_all()
        return json.dumps(
            [
                {
                    "id": s.id,
                    "title": s.title,
                    "skill_name": s.skill_name,
                    "status": s.status,
                    "risk_flags": s.risk_flags,
                    "validation_errors": s.validation_errors,
                    "updated_at": s.updated_at.isoformat(),
                }
                for s in items
            ],
            ensure_ascii=False,
            indent=2,
        )
    suggestion_id = str(args.get("id") or "")
    if not suggestion_id:
        return "Error: 'id' is required for this action."
    suggestion = store.get(suggestion_id)
    if suggestion is None:
        return f"Error: skill suggestion not found: {suggestion_id}"
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
        target_dir = (
            args.get("target_dir")
            or config.get("target_dir")
            or skills_config.get("local_dir")
        )
        if not target_dir:
            return "Error: target_dir is required."
        result = materialize_skill_suggestion(
            suggestion,
            target_dir=Path(str(target_dir)).expanduser(),
            overwrite=bool(args.get("overwrite", False)),
        )
        updated = store.update_status(
            suggestion_id, "materialized", materialized_path=str(result.skill_dir)
        )
        return json.dumps(
            {
                "status": "materialized",
                "skill_dir": str(result.skill_dir),
                "files": [str(p) for p in result.files],
                "suggestion": updated.to_dict() if updated else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    return f"Error: unknown skill_learning action '{action}'."
