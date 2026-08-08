"""Persona parsing engine and prompt injector."""

from simpleclaw.persona.assembler import assemble_prompt
from simpleclaw.persona.composition_projection import (
    build_composition_persona_projection,
)
from simpleclaw.persona.models import (
    CompositionPersonaProjection,
    FileType,
    PersonaFile,
    PromptAssembly,
    Section,
    SourceScope,
)
from simpleclaw.persona.parser import parse_markdown
from simpleclaw.persona.resolver import resolve_persona_files

__all__ = [
    "FileType",
    "CompositionPersonaProjection",
    "PersonaFile",
    "PromptAssembly",
    "Section",
    "SourceScope",
    "assemble_prompt",
    "build_composition_persona_projection",
    "parse_markdown",
    "resolve_persona_files",
]
