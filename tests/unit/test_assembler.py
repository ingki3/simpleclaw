"""Tests for the prompt assembler."""

import pytest

from simpleclaw.persona.models import (
    FileType,
    PersonaFile,
    Section,
    SourceScope,
)
from simpleclaw.persona.assembler import assemble_prompt


def _make_persona(file_type: FileType, content_text: str) -> PersonaFile:
    """Helper to create a PersonaFile with a single section."""
    return PersonaFile(
        file_type=file_type,
        source_path=f"/fake/{file_type.value}.md",
        source_scope=SourceScope.LOCAL,
        sections=[Section(level=1, title=file_type.value.upper(), content=content_text)],
        raw_content=content_text,
    )


class TestAssemblePromptFull:
    """Test assembly with all three files."""

    def test_three_files_order(self):
        agent = _make_persona(FileType.AGENT, "I am the agent.")
        user = _make_persona(FileType.USER, "User info here.")
        memory = _make_persona(FileType.MEMORY, "Past events.")

        result = assemble_prompt([memory, agent, user], token_budget=4096)

        assert result.assembled_text.index("AGENT") < result.assembled_text.index("USER")
        assert result.assembled_text.index("USER") < result.assembled_text.index("MEMORY")
        assert not result.was_truncated
        assert result.token_count > 0
        assert result.token_count <= result.token_budget

    def test_separator_between_files(self):
        agent = _make_persona(FileType.AGENT, "Agent content.")
        user = _make_persona(FileType.USER, "User content.")

        result = assemble_prompt([agent, user], token_budget=4096)
        assert "---" in result.assembled_text


class TestAssemblePromptPartial:
    """Test assembly with fewer than 3 files."""

    def test_agent_only(self):
        agent = _make_persona(FileType.AGENT, "Solo agent.")
        result = assemble_prompt([agent], token_budget=4096)
        assert "Solo agent" in result.assembled_text
        assert not result.was_truncated

    def test_agent_and_user_no_memory(self):
        agent = _make_persona(FileType.AGENT, "Agent text.")
        user = _make_persona(FileType.USER, "User text.")
        result = assemble_prompt([agent, user], token_budget=4096)
        assert "Agent text" in result.assembled_text
        assert "User text" in result.assembled_text

    def test_empty_list(self):
        result = assemble_prompt([], token_budget=4096)
        assert result.assembled_text == ""
        assert result.token_count == 0
        assert not result.was_truncated


class TestAssemblePromptTruncation:
    """Test token budget truncation."""

    def test_budget_not_exceeded(self):
        agent = _make_persona(FileType.AGENT, "Short.")
        result = assemble_prompt([agent], token_budget=4096)
        assert result.token_count <= result.token_budget

    def test_budget_exactly_at_limit(self):
        agent = _make_persona(FileType.AGENT, "A")
        result = assemble_prompt([agent], token_budget=4096)
        assert result.token_count <= 4096

    def test_memory_truncated_first(self):
        agent = _make_persona(FileType.AGENT, "Agent. " * 10)
        user = _make_persona(FileType.USER, "User. " * 10)
        memory = _make_persona(FileType.MEMORY, "Memory data. " * 500)

        result = assemble_prompt([agent, user, memory], token_budget=100)
        assert result.was_truncated
        assert result.token_count <= 100
        # Agent content should be preserved
        assert "Agent" in result.assembled_text

    def test_zero_length_memory_after_truncation(self):
        agent = _make_persona(FileType.AGENT, "Important agent info. " * 50)
        memory = _make_persona(FileType.MEMORY, "Some memory. " * 10)

        result = assemble_prompt([agent, memory], token_budget=80)
        assert result.token_count <= 80

    def test_all_files_exceed_budget(self):
        """When AGENT+USER alone exceed budget, aggressive truncation."""
        agent = _make_persona(FileType.AGENT, "Agent word. " * 200)
        user = _make_persona(FileType.USER, "User word. " * 200)

        result = assemble_prompt([agent, user], token_budget=50)
        assert result.was_truncated
        assert result.token_count <= 50
