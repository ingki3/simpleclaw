"""operator-only skill_learning native tool handler.

BIZ-429 — 운영자 승인 UX 를 담당한다. ``show``/``diff`` 는 운영자가 설치 판단에
필요한 요약·위험 신호·검증 오류·파일 미리보기를 제공하고, ``materialize`` 는
``accepted`` 상태 + ``confirm=true`` 두 게이트를 모두 통과해야만 실제 runtime
skill 로 설치한다(자동 materialize 경로 없음).
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

from simpleclaw.skills.learning import SkillSuggestion, SkillSuggestionStore
from simpleclaw.skills.materializer import materialize_skill_suggestion

# show 응답의 SKILL.md 미리보기 상한 — LLM 컨텍스트 폭주 방지.
_SKILL_MD_PREVIEW_CHARS = 4000


def handle_skill_learning(
    args: dict[str, Any], *, config: dict, skills_config: dict
) -> str:
    """pending skill 후보를 조회/승인/거절/diff/materialize 한다."""
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
            [_summary_payload(s) for s in items], ensure_ascii=False, indent=2
        )
    suggestion_id = str(args.get("id") or "")
    if not suggestion_id:
        return "Error: 'id' is required for this action."
    suggestion = store.get(suggestion_id)
    if suggestion is None:
        return f"Error: skill suggestion not found: {suggestion_id}"
    if action == "show":
        return json.dumps(_review_payload(suggestion), ensure_ascii=False, indent=2)
    if action == "diff":
        target_dir = _resolve_target_dir(args, config, skills_config)
        if not target_dir:
            return "Error: target_dir is required."
        return json.dumps(
            _diff_payload(suggestion, target_dir=target_dir),
            ensure_ascii=False,
            indent=2,
        )
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
        return _handle_materialize(
            args,
            suggestion,
            store=store,
            config=config,
            skills_config=skills_config,
        )
    return f"Error: unknown skill_learning action '{action}'."


def _handle_materialize(
    args: dict[str, Any],
    suggestion: SkillSuggestion,
    *,
    store: SkillSuggestionStore,
    config: dict,
    skills_config: dict,
) -> str:
    """accepted + confirm 이중 게이트를 통과한 후보만 설치한다."""
    # 게이트 1 — 운영자 승인. require_operator_accept=False 로 명시했을 때만 완화.
    require_accept = bool(config.get("require_operator_accept", True))
    if require_accept and suggestion.status != "accepted":
        return (
            "Error: suggestion must be accepted before materialize "
            f"(current status: {suggestion.status}). "
            "Run action='accept' first after operator review."
        )
    # 게이트 2 — 명시적 confirm. 검토 없이 곧바로 설치되는 사고를 막는다.
    if not bool(args.get("confirm", False)):
        return (
            "Error: materialize requires confirm=true. "
            "Review with action='show' and action='diff' first."
        )
    if suggestion.validation_errors:
        return (
            "Error: suggestion has validation errors and cannot be materialized: "
            + "; ".join(suggestion.validation_errors)
        )
    target_dir = _resolve_target_dir(args, config, skills_config)
    if not target_dir:
        return "Error: target_dir is required."
    try:
        result = materialize_skill_suggestion(
            suggestion,
            target_dir=target_dir,
            overwrite=bool(args.get("overwrite", False)),
            require_accepted=require_accept,
        )
    except (ValueError, FileExistsError, PermissionError, SyntaxError) as exc:
        return f"Error: materialize failed: {exc}"
    updated = store.update_status(
        suggestion.id, "materialized", materialized_path=str(result.skill_dir)
    )
    return json.dumps(
        {
            "status": "materialized",
            "skill_dir": str(result.skill_dir),
            "files": [str(p) for p in result.files],
            "backup_dir": str(result.backup_dir) if result.backup_dir else None,
            "suggestion": updated.to_dict() if updated else None,
        },
        ensure_ascii=False,
        indent=2,
    )


def _resolve_target_dir(
    args: dict[str, Any], config: dict, skills_config: dict
) -> Path | None:
    """diff/materialize 대상 skill 루트를 인자 → 학습 설정 → 스킬 설정 순으로 정한다."""
    raw = (
        args.get("target_dir")
        or config.get("target_dir")
        or skills_config.get("local_dir")
    )
    return Path(str(raw)).expanduser() if raw else None


def _summary_payload(suggestion: SkillSuggestion) -> dict[str, Any]:
    """list 응답 한 건 — 운영자가 훑어보고 show 대상을 고를 수 있는 최소 요약."""
    return {
        "id": suggestion.id,
        "title": suggestion.title,
        "skill_name": suggestion.skill_name,
        "status": suggestion.status,
        "risk_flags": suggestion.risk_flags,
        "validation_errors": suggestion.validation_errors,
        "files_to_write": _planned_paths(suggestion),
        "updated_at": suggestion.updated_at.isoformat(),
    }


def _review_payload(suggestion: SkillSuggestion) -> dict[str, Any]:
    """show 응답 — 운영자 승인 판단에 필요한 정보를 한 화면에 모은다."""
    data = suggestion.to_dict()
    # 원문 전체 대신 검토용 미리보기/요약을 제공한다 — 파일 본문은 diff 로 확인.
    data.pop("scripts", None)
    data.pop("references", None)
    data.pop("trace", None)
    skill_md = suggestion.skill_md
    data["skill_md"] = skill_md[:_SKILL_MD_PREVIEW_CHARS]
    data["skill_md_truncated"] = len(skill_md) > _SKILL_MD_PREVIEW_CHARS
    data["files_to_write"] = [
        {"path": path, "chars": len(content)}
        for path, content in _planned_files(suggestion).items()
    ]
    data["source_trace_summary"] = {
        "steps": len(suggestion.trace),
        "tools": [
            {"tool_name": step.tool_name, "success": step.success}
            for step in suggestion.trace
        ],
        "source_msg_ids": suggestion.source_msg_ids,
    }
    return data


def _diff_payload(
    suggestion: SkillSuggestion, *, target_dir: Path
) -> dict[str, Any]:
    """설치 시 실제로 일어날 파일 변경을 create/update preview diff 로 만든다."""
    skill_dir = target_dir / suggestion.skill_name
    planned = _planned_files(suggestion)
    entries: list[dict[str, Any]] = []
    for rel_path, content in planned.items():
        existing_file = skill_dir / rel_path
        if existing_file.is_file():
            try:
                old = existing_file.read_text(encoding="utf-8")
            except OSError:
                old = ""
            mode = "unchanged" if old == content else "update"
        else:
            old, mode = "", "create"
        diff_text = ""
        if mode != "unchanged":
            diff_text = "".join(
                difflib.unified_diff(
                    old.splitlines(keepends=True),
                    content.splitlines(keepends=True),
                    fromfile=f"a/{rel_path}",
                    tofile=f"b/{rel_path}",
                )
            )
        entries.append({"path": rel_path, "mode": mode, "diff": diff_text})
    # overwrite 시 새 plan 에 없는 기존 파일은 백업으로만 남고 사라진다 — 명시한다.
    removed = []
    if skill_dir.is_dir():
        removed = sorted(
            str(p.relative_to(skill_dir))
            for p in skill_dir.rglob("*")
            if p.is_file() and str(p.relative_to(skill_dir)) not in planned
        )
    return {
        "id": suggestion.id,
        "skill_name": suggestion.skill_name,
        "skill_dir": str(skill_dir),
        "mode": "update" if skill_dir.exists() else "create",
        "files": entries,
        "removed_on_overwrite": removed,
    }


def _planned_files(suggestion: SkillSuggestion) -> dict[str, str]:
    """materializer 가 쓰게 될 파일 집합과 동일한 순서의 매핑."""
    files: dict[str, str] = {"SKILL.md": suggestion.skill_md}
    files.update(suggestion.scripts)
    files.update(suggestion.references)
    return files


def _planned_paths(suggestion: SkillSuggestion) -> list[str]:
    return list(_planned_files(suggestion).keys())
