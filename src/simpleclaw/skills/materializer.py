"""승인된 skill learning 후보를 runtime skill package로 쓰는 materializer."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from simpleclaw.skills.learning import (
    SkillSuggestion,
    normalize_skill_name,
    validate_skill_package_plan,
)


@dataclass(frozen=True)
class MaterializeResult:
    """materialize 결과 경로 요약."""

    skill_dir: Path
    files: list[Path]


def materialize_skill_suggestion(
    suggestion: SkillSuggestion,
    *,
    target_dir: str | Path,
    overwrite: bool = False,
) -> MaterializeResult:
    """승인된 후보를 ``<target>/<skill>/`` 패키지로 안전하게 작성한다."""
    skill_name = normalize_skill_name(suggestion.skill_name)
    errors = validate_skill_package_plan(
        skill_name=skill_name,
        skill_md=suggestion.skill_md,
        scripts=suggestion.scripts,
        references=suggestion.references,
    )
    if errors:
        raise ValueError("Invalid skill package plan: " + "; ".join(errors))

    root = Path(target_dir).expanduser().resolve()
    skill_dir = (root / skill_name).resolve()
    if not skill_dir.is_relative_to(root):
        raise ValueError("Skill directory escapes target root.")
    if skill_dir.exists():
        if not overwrite:
            raise FileExistsError(f"Skill already exists: {skill_dir}")
        shutil.rmtree(skill_dir)

    files: dict[str, str] = {"SKILL.md": suggestion.skill_md}
    files.update(suggestion.scripts)
    files.update(suggestion.references)
    for rel_path, content in files.items():
        target = (skill_dir / rel_path).resolve()
        if not target.is_relative_to(skill_dir):
            raise ValueError(f"Unsafe output path: {rel_path}")
        if target.suffix == ".py":
            compile(content, str(target), "exec")

    tmp_dir = skill_dir.with_name(f".{skill_dir.name}.tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=False)
    written: list[Path] = []
    try:
        for rel_path, content in files.items():
            target = (tmp_dir / rel_path).resolve()
            if not target.is_relative_to(tmp_dir):
                raise ValueError(f"Unsafe output path: {rel_path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(skill_dir / rel_path)
        tmp_dir.replace(skill_dir)
    except Exception:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        raise
    return MaterializeResult(skill_dir=skill_dir, files=written)
