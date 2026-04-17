"""Skill discovery: scan directories and parse SKILL.md files."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from simpleclaw.skills.models import SkillDefinition, SkillScope

logger = logging.getLogger(__name__)


def discover_skills(
    local_dir: str | Path,
    global_dir: str | Path,
) -> list[SkillDefinition]:
    """Discover skills from local and global directories.

    Local skills override global skills with the same name.
    """
    local_path = Path(local_dir).expanduser()
    global_path = Path(global_dir).expanduser()

    skills: dict[str, SkillDefinition] = {}

    # Scan global first (lower priority)
    _scan_skills_dir(global_path, SkillScope.GLOBAL, skills)

    # Scan local second (higher priority, overrides global)
    _scan_skills_dir(local_path, SkillScope.LOCAL, skills)

    return list(skills.values())


def _scan_skills_dir(
    directory: Path,
    scope: SkillScope,
    skills: dict[str, SkillDefinition],
) -> None:
    """Scan a directory for skill subdirectories containing SKILL.md."""
    if not directory.is_dir():
        logger.debug("Skills directory does not exist: %s", directory)
        return

    for entry in sorted(directory.iterdir()):
        if not entry.is_dir():
            continue

        skill_md = entry / "SKILL.md"
        if not skill_md.is_file():
            logger.warning("No SKILL.md found in %s, skipping.", entry)
            continue

        skill = _parse_skill_md(skill_md, scope)
        if skill:
            if skill.name in skills and scope == SkillScope.LOCAL:
                logger.info(
                    "Local skill '%s' overrides global.", skill.name
                )
            skills[skill.name] = skill


def _parse_skill_md(skill_md: Path, scope: SkillScope) -> SkillDefinition | None:
    """Parse a SKILL.md file into a SkillDefinition."""
    try:
        content = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("Failed to read %s: %s", skill_md, e)
        return None

    # Extract name from first # heading
    name_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    if not name_match:
        logger.warning("No skill name found in %s", skill_md)
        return None
    name = name_match.group(1).strip()

    # Extract description (text between first heading and next section)
    desc_match = re.search(
        r"^#\s+.+\n\n(.+?)(?=\n##|\Z)", content, re.MULTILINE | re.DOTALL
    )
    description = desc_match.group(1).strip() if desc_match else ""

    # Extract script target from ## Script section
    script_match = re.search(
        r"##\s+Script\s*\n+.*?Target:\s*`?([^`\n]+)`?",
        content,
        re.MULTILINE,
    )
    script_path = ""
    if script_match:
        script_path = str(skill_md.parent / script_match.group(1).strip())

    # Extract trigger from ## Trigger section
    trigger_match = re.search(
        r"##\s+Trigger\s*\n+(.+?)(?=\n##|\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    trigger = trigger_match.group(1).strip() if trigger_match else ""

    return SkillDefinition(
        name=name,
        description=description,
        script_path=script_path,
        trigger=trigger,
        scope=scope,
        skill_dir=str(skill_md.parent),
    )
