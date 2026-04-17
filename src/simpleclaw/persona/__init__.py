"""Persona parsing engine and prompt injector."""

from simpleclaw.persona.models import (
    FileType,
    PersonaFile,
    PromptAssembly,
    Section,
    SourceScope,
)
from simpleclaw.persona.parser import parse_markdown
from simpleclaw.persona.resolver import resolve_persona_files
from simpleclaw.persona.assembler import assemble_prompt

__all__ = [
    "FileType",
    "PersonaFile",
    "PromptAssembly",
    "Section",
    "SourceScope",
    "parse_markdown",
    "resolve_persona_files",
    "assemble_prompt",
]
