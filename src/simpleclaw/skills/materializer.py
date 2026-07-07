"""승인된 skill learning 후보를 runtime skill package로 쓰는 materializer.

BIZ-429 — materialize 는 운영자 승인(accepted) 이후에만 허용되는 방어를
tool layer 와 별개로 이 계층에서도 강제한다(``require_accepted``). overwrite
시에는 기존 패키지를 삭제하지 않고 백업 디렉터리로 이동해 파괴적 덮어쓰기를
복구 가능하게 만든다.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
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
    # overwrite 로 기존 패키지를 대체한 경우, 이동된 백업 디렉터리 경로.
    backup_dir: Path | None = None


def materialize_skill_suggestion(
    suggestion: SkillSuggestion,
    *,
    target_dir: str | Path,
    overwrite: bool = False,
    require_accepted: bool = True,
) -> MaterializeResult:
    """승인된 후보를 ``<target>/<skill>/`` 패키지로 안전하게 작성한다.

    Args:
        suggestion: materialize 대상 후보.
        target_dir: skill package 루트 디렉터리.
        overwrite: 동일 skill 이 이미 있으면 백업 후 교체할지 여부.
        require_accepted: True 면 suggestion.status 가 ``accepted`` 가 아닐 때
            거부한다. tool layer 의 승인 게이트가 우회되더라도 이 계층에서
            자동 설치를 한 번 더 차단하기 위한 방어.
    """
    # 승인 게이트 — 검증보다 먼저 확인해 미승인 후보는 어떤 파일 작업도 하지 않는다.
    if require_accepted and suggestion.status != "accepted":
        raise PermissionError(
            "Skill suggestion must be accepted before materialization "
            f"(current status: {suggestion.status})."
        )
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
    if skill_dir.exists() and not overwrite:
        raise FileExistsError(f"Skill already exists: {skill_dir}")

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
    backup_dir: Path | None = None
    try:
        for rel_path, content in files.items():
            target = (tmp_dir / rel_path).resolve()
            if not target.is_relative_to(tmp_dir):
                raise ValueError(f"Unsafe output path: {rel_path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(skill_dir / rel_path)
        # overwrite 는 삭제 대신 백업 이동 — 잘못된 승인도 되돌릴 수 있어야 한다.
        if skill_dir.exists():
            backup_dir = _next_backup_dir(skill_dir)
            skill_dir.replace(backup_dir)
        tmp_dir.replace(skill_dir)
    except Exception:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        # 새 패키지 반영 전에 실패했으면 백업을 원위치로 복원한다.
        if backup_dir is not None and backup_dir.exists() and not skill_dir.exists():
            backup_dir.replace(skill_dir)
        raise
    return MaterializeResult(skill_dir=skill_dir, files=written, backup_dir=backup_dir)


def _next_backup_dir(skill_dir: Path) -> Path:
    """충돌하지 않는 백업 디렉터리 경로를 만든다."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = skill_dir.with_name(f".{skill_dir.name}.bak.{stamp}")
    seq = 1
    while candidate.exists():
        candidate = skill_dir.with_name(f".{skill_dir.name}.bak.{stamp}.{seq}")
        seq += 1
    return candidate
