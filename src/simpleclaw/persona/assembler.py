"""System prompt assembler with token budget management."""

from __future__ import annotations

import logging

import tiktoken

from simpleclaw.persona.models import FileType, PersonaFile, PromptAssembly

logger = logging.getLogger(__name__)

_SECTION_SEPARATOR = "\n\n---\n\n"
_FILE_ORDER = [FileType.AGENT, FileType.USER, FileType.MEMORY]


def _count_tokens(text: str, encoding_name: str = "cl100k_base") -> int:
    """Count tokens in text using tiktoken."""
    enc = tiktoken.get_encoding(encoding_name)
    return len(enc.encode(text))


def _render_persona_file(persona_file: PersonaFile) -> str:
    """Render a PersonaFile's sections into a text block."""
    if not persona_file.sections:
        return ""

    parts = []
    for section in persona_file.sections:
        if section.title:
            prefix = "#" * section.level
            parts.append(f"{prefix} {section.title}")
        if section.content:
            parts.append(section.content)

    return "\n\n".join(parts)


def assemble_prompt(
    persona_files: list[PersonaFile],
    token_budget: int,
) -> PromptAssembly:
    """Assemble persona files into a system prompt within token budget.

    Files are ordered AGENT -> USER -> MEMORY. If the assembled text
    exceeds the token budget, MEMORY content is truncated from the end
    first, then USER if still over budget.
    """
    if not persona_files:
        return PromptAssembly(token_budget=token_budget)

    # Sort files by the canonical order
    files_by_type: dict[FileType, PersonaFile] = {}
    for pf in persona_files:
        files_by_type[pf.file_type] = pf

    ordered_files = [files_by_type[ft] for ft in _FILE_ORDER if ft in files_by_type]

    # Render each file
    rendered: list[tuple[PersonaFile, str]] = []
    for pf in ordered_files:
        text = _render_persona_file(pf)
        if text:
            rendered.append((pf, text))

    if not rendered:
        return PromptAssembly(parts=ordered_files, token_budget=token_budget)

    # Assemble full text
    full_text = _SECTION_SEPARATOR.join(text for _, text in rendered)
    total_tokens = _count_tokens(full_text)

    if total_tokens <= token_budget:
        return PromptAssembly(
            parts=ordered_files,
            assembled_text=full_text,
            token_count=total_tokens,
            token_budget=token_budget,
            was_truncated=False,
        )

    # Truncation needed — remove content from the end (MEMORY first, then USER)
    logger.info(
        "Token budget exceeded (%d > %d), truncating.", total_tokens, token_budget
    )
    truncated_texts = [text for _, text in rendered]
    was_truncated = False

    # Try truncating from the last file backwards
    for i in range(len(truncated_texts) - 1, 0, -1):
        assembled = _SECTION_SEPARATOR.join(truncated_texts)
        current_tokens = _count_tokens(assembled)

        if current_tokens <= token_budget:
            break

        # Progressively shorten this file's text
        truncated_texts[i] = _truncate_text_to_fit(
            truncated_texts[i],
            current_tokens - token_budget,
        )
        was_truncated = True

    assembled = _SECTION_SEPARATOR.join(t for t in truncated_texts if t)
    final_tokens = _count_tokens(assembled)

    # If still over budget after removing optional files, truncate aggressively
    if final_tokens > token_budget:
        enc = tiktoken.get_encoding("cl100k_base")
        tokens = enc.encode(assembled)[:token_budget]
        assembled = enc.decode(tokens)
        final_tokens = token_budget
        was_truncated = True

    return PromptAssembly(
        parts=ordered_files,
        assembled_text=assembled,
        token_count=final_tokens,
        token_budget=token_budget,
        was_truncated=was_truncated,
    )


def _truncate_text_to_fit(text: str, tokens_to_remove: int) -> str:
    """Remove approximately tokens_to_remove tokens from the end of text."""
    enc = tiktoken.get_encoding("cl100k_base")
    tokens = enc.encode(text)
    if tokens_to_remove >= len(tokens):
        return ""
    kept = tokens[: len(tokens) - tokens_to_remove]
    return enc.decode(kept)
