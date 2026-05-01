"""스킬 탐색 모듈: 디렉터리를 스캔하여 SKILL.md 파일을 파싱한다.

동작 흐름:
1. 글로벌 스킬 디렉터리를 먼저 스캔 (낮은 우선순위)
2. 로컬 스킬 디렉터리를 스캔하여 동일 이름의 글로벌 스킬을 덮어씀
3. 각 SKILL.md는 YAML frontmatter 또는 마크다운 헤딩 방식으로 파싱

설계 결정:
- 로컬 스킬이 글로벌 스킬보다 우선하므로, 프로젝트별 커스터마이징 가능
- 파싱 실패 시 해당 스킬만 건너뛰고 나머지는 계속 로드
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from simpleclaw.skills.models import RetryPolicy, SkillDefinition, SkillScope

logger = logging.getLogger(__name__)


def discover_skills(
    local_dir: str | Path,
    global_dir: str | Path,
) -> list[SkillDefinition]:
    """로컬 및 글로벌 디렉터리에서 스킬을 탐색한다.

    동일 이름의 스킬이 양쪽에 존재하면 로컬 스킬이 우선한다.

    Args:
        local_dir: 프로젝트별 스킬 디렉터리 경로
        global_dir: 사용자 전역 스킬 디렉터리 경로

    Returns:
        파싱된 SkillDefinition 목록
    """
    local_path = Path(local_dir).expanduser()
    global_path = Path(global_dir).expanduser()

    skills: dict[str, SkillDefinition] = {}

    # 글로벌을 먼저 스캔 (낮은 우선순위)
    _scan_skills_dir(global_path, SkillScope.GLOBAL, skills)

    # 로컬을 나중에 스캔 (높은 우선순위, 글로벌을 덮어씀)
    _scan_skills_dir(local_path, SkillScope.LOCAL, skills)

    return list(skills.values())


def _scan_skills_dir(
    directory: Path,
    scope: SkillScope,
    skills: dict[str, SkillDefinition],
) -> None:
    """디렉터리 내 SKILL.md를 포함하는 하위 디렉터리를 스캔한다.

    Args:
        directory: 스캔 대상 디렉터리
        scope: 스킬의 범위 (LOCAL 또는 GLOBAL)
        skills: 이름을 키로 하는 스킬 사전 (결과가 여기에 누적됨)
    """
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
    """SKILL.md 파일을 파싱하여 SkillDefinition으로 변환한다.

    두 가지 포맷을 지원한다:
    1. YAML frontmatter (---name: ...---) — OpenClaw/AgentSkills 스타일
    2. 마크다운 헤딩 + ## Script/Target: — 레거시 스타일

    또한 bash/shell 코드 블록에서 실행 가능한 명령어를 추출한다.

    Args:
        skill_md: SKILL.md 파일 경로
        scope: 스킬의 범위 (LOCAL 또는 GLOBAL)

    Returns:
        파싱 성공 시 SkillDefinition, 실패 시 None
    """
    try:
        content = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("Failed to read %s: %s", skill_md, e)
        return None

    name = ""
    description = ""
    retry_policy: RetryPolicy | None = None

    # YAML frontmatter를 먼저 시도 (---\n...\n---)
    fm_match = re.match(r"^---\s*\n(.+?)\n---\s*\n", content, re.DOTALL)
    if fm_match:
        try:
            fm = yaml.safe_load(fm_match.group(1))
            if isinstance(fm, dict):
                name = fm.get("name", "")
                description = fm.get("description", "")
                retry_policy = _parse_retry_policy(fm.get("retry"), skill_md)
        except yaml.YAMLError:
            pass

    # 폴백: 첫 번째 # 헤딩에서 이름 추출
    if not name:
        name_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        if not name_match:
            logger.warning("No skill name found in %s", skill_md)
            return None
        name = name_match.group(1).strip()

    # 폴백: 설명 추출
    if not description:
        desc_match = re.search(
            r"^#\s+.+\n\n(.+?)(?=\n##|\Z)", content, re.MULTILINE | re.DOTALL
        )
        description = desc_match.group(1).strip() if desc_match else ""

    # ## Script 섹션에서 스크립트 대상 경로 추출 (레거시 포맷)
    script_match = re.search(
        r"##\s+Script\s*\n+.*?Target:\s*`?([^`\n]+)`?",
        content,
        re.MULTILINE,
    )
    script_path = ""
    if script_match:
        script_path = str(skill_md.parent / script_match.group(1).strip())

    # bash/shell 코드 블록에서 실행 가능한 명령어 추출
    commands: list[str] = []
    code_blocks = re.findall(
        r"```(?:bash|shell|sh)\s*\n(.+?)```",
        content,
        re.DOTALL,
    )
    for block in code_blocks:
        for line in block.strip().split("\n"):
            line = line.strip()
            if line and not line.startswith("#"):
                commands.append(line)

    # ## Trigger 또는 ## When to use 섹션에서 트리거 조건 추출
    trigger_match = re.search(
        r"##\s+(?:Trigger|When to [Uu]se(?:\s*\(.+?\))?)\s*\n+(.+?)(?=\n##|\Z)",
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
        commands=commands,
        retry_policy=retry_policy,
    )


def _parse_retry_policy(
    raw: object, skill_md: Path
) -> RetryPolicy | None:
    """프론트매터의 ``retry`` 값을 ``RetryPolicy``로 변환한다.

    허용 형식 예::

        retry:
          max_retries: 3
          initial_backoff_seconds: 0.5
          backoff_factor: 2.0
          max_backoff_seconds: 10
          idempotent: true
          retry_on_timeout: false

    모든 필드는 옵션이며, 잘못된 타입은 무시되고 ``RetryPolicy`` 기본값이 적용된다.
    파싱이 실패하거나 ``raw``가 매핑이 아니면 None을 반환해 정책을 비활성화한다.

    Args:
        raw: ``retry:`` 키 값 (보통 dict).
        skill_md: 경고 로그용 스킬 파일 경로.

    Returns:
        파싱된 ``RetryPolicy`` 또는 None.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        logger.warning(
            "Invalid 'retry' block in %s: expected mapping, got %s",
            skill_md, type(raw).__name__,
        )
        return None

    defaults = RetryPolicy()

    def _coerce(key: str, default: object, caster):
        value = raw.get(key, default)
        try:
            return caster(value)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid retry.%s in %s: %r (using default %r)",
                key, skill_md, value, default,
            )
            return default

    return RetryPolicy(
        max_retries=_coerce("max_retries", defaults.max_retries, int),
        initial_backoff_seconds=_coerce(
            "initial_backoff_seconds", defaults.initial_backoff_seconds, float
        ),
        backoff_factor=_coerce(
            "backoff_factor", defaults.backoff_factor, float
        ),
        max_backoff_seconds=_coerce(
            "max_backoff_seconds", defaults.max_backoff_seconds, float
        ),
        idempotent=bool(raw.get("idempotent", defaults.idempotent)),
        retry_on_timeout=bool(
            raw.get("retry_on_timeout", defaults.retry_on_timeout)
        ),
    )
