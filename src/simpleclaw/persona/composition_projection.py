"""Runtime persona를 Final Composer용 최소 projection으로 변환한다."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace

from simpleclaw.persona.assembler import assemble_prompt
from simpleclaw.persona.models import (
    CompositionPersonaProjection,
    FileType,
    PersonaFile,
    Section,
)

COMPOSITION_PERSONA_POLICY_VERSION = "composition_persona_v1"

DEFAULT_COMPOSITION_SECTIONS: dict[FileType, tuple[str, ...]] = {
    FileType.SOUL: ("Identity", "Personality", "Speaking Style", "Core Values"),
    FileType.AGENT: ("Identity", "Language"),
    FileType.USER: (
        "Preferences",
        "Corrections",
        "Preferences and Corrections",
        "Stale Memory Guards",
    ),
}

_SECRET_LINE_RE = re.compile(
    r"(?:"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\b(?:api[_ -]?key|access[_ -]?token|auth[_ -]?token|password|passwd|secret)\b\s*[:=]|"
    r"\b(?:sk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{12,}\b"
    r")",
    re.IGNORECASE,
)


def _normalized_title(value: str) -> str:
    return " ".join(value.casefold().split())


def _without_secret_lines(value: str) -> str:
    """Credential처럼 보이는 줄은 내용 일부도 노출하지 않고 제거한다."""
    return "\n".join(
        line for line in value.splitlines() if not _SECRET_LINE_RE.search(line)
    ).strip()


def _policy_sections(
    section_policy: Mapping[str, Sequence[str]] | None,
) -> dict[FileType, tuple[str, ...]]:
    if section_policy is None:
        return DEFAULT_COMPOSITION_SECTIONS
    normalized: dict[FileType, tuple[str, ...]] = {}
    for file_type in (FileType.SOUL, FileType.AGENT, FileType.USER):
        values = section_policy.get(file_type.value)
        if not isinstance(values, Sequence) or isinstance(values, str | bytes):
            values = DEFAULT_COMPOSITION_SECTIONS[file_type]
        maximum = {
            _normalized_title(title): title
            for title in DEFAULT_COMPOSITION_SECTIONS[file_type]
        }
        selected: list[str] = []
        for value in values:
            normalized_value = _normalized_title(str(value))
            canonical = maximum.get(normalized_value)
            if canonical is not None and canonical not in selected:
                selected.append(canonical)
        normalized[file_type] = tuple(selected)
    return normalized


def build_composition_persona_projection(
    persona_files: Sequence[PersonaFile],
    *,
    token_budget: int,
    section_policy: Mapping[str, Sequence[str]] | None = None,
    policy_version: str = COMPOSITION_PERSONA_POLICY_VERSION,
) -> CompositionPersonaProjection:
    """Allowlisted runtime sections만 deterministic composer projection으로 만든다."""
    if token_budget <= 0:
        raise ValueError("composition persona token_budget must be positive")
    if not policy_version.strip():
        raise ValueError("composition persona policy_version is required")

    policy = _policy_sections(section_policy)
    filtered: list[PersonaFile] = []
    for persona_file in persona_files:
        allowed = policy.get(persona_file.file_type)
        if allowed is None:  # MEMORY 및 미지원 source는 fail-closed 제외한다.
            continue
        allowed_titles = {_normalized_title(title) for title in allowed}
        sections: list[Section] = []
        for section in persona_file.sections:
            if _normalized_title(section.title) not in allowed_titles:
                continue
            content = _without_secret_lines(section.content)
            if content:
                sections.append(replace(section, content=content))
        if sections:
            filtered.append(replace(persona_file, sections=sections, raw_content=""))

    assembly = assemble_prompt(filtered, token_budget)
    instruction_text = (assembly.assembled_text or "").strip()
    included_types = tuple(persona.file_type for persona in assembly.parts)
    policy_payload = {
        "policy_version": policy_version,
        "sections": {
            file_type.value: list(policy[file_type])
            for file_type in (FileType.SOUL, FileType.AGENT, FileType.USER)
        },
        "source_types": [file_type.value for file_type in included_types],
        "token_budget": token_budget,
        "content_hash": hashlib.sha256(instruction_text.encode("utf-8")).hexdigest(),
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            policy_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return CompositionPersonaProjection(
        instruction_text=instruction_text,
        source_types=included_types,
        token_count=assembly.token_count,
        token_budget=token_budget,
        policy_version=policy_version,
        fingerprint=fingerprint,
    )
