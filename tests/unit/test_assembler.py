"""Tests for the prompt assembler."""

from simpleclaw.persona.assembler import assemble_prompt
from simpleclaw.persona.models import (
    FileType,
    PersonaFile,
    Section,
    SourceScope,
)


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

    def test_managed_dreaming_sections_are_compacted(self):
        """오래된 Dreaming managed 블록은 매 turn 시스템 프롬프트에서 제거한다."""
        agent = _make_persona(
            FileType.AGENT,
            "Before\n"
            "<!-- managed:dreaming:dreaming-updates -->\n"
            "## Dreaming Updates (2026-05-01)\n"
            "- 오래된 크론 변경\n"
            "## Dreaming Updates (2026-05-02)\n"
            "- 반복 업데이트\n"
            "<!-- /managed:dreaming:dreaming-updates -->\n"
            "After",
        )
        user = _make_persona(
            FileType.USER,
            "User before\n"
            "<!-- managed:dreaming:insights -->\n"
            "## Dreaming Insights (2026-04-28)\n"
            "- 오래된 관심사\n"
            "## Dreaming Insights (2026-05-06)\n"
            "- 반복 관심사\n"
            "<!-- /managed:dreaming:insights -->\n"
            "User after",
        )

        result = assemble_prompt([agent, user], token_budget=4096)

        assert "Before" in result.assembled_text
        assert "After" in result.assembled_text
        assert "User before" in result.assembled_text
        assert "User after" in result.assembled_text
        assert "오래된 크론 변경" not in result.assembled_text
        assert "오래된 관심사" not in result.assembled_text
        assert "Dreaming Updates" not in result.assembled_text
        assert "Dreaming Insights" not in result.assembled_text
        assert "Dreaming-managed memory omitted" not in result.assembled_text


class TestAssemblePromptSoul:
    """BIZ-451: SOUL 파일이 assemble order에 포함되는지 검증."""

    def test_soul_included_and_first(self):
        soul = _make_persona(FileType.SOUL, "사용자를 형님이라고 부를 것. 반말은 하지 말것.")
        agent = _make_persona(FileType.AGENT, "I am the agent.")
        user = _make_persona(FileType.USER, "User info here.")
        memory = _make_persona(FileType.MEMORY, "Past events.")

        result = assemble_prompt([memory, agent, soul, user], token_budget=4096)

        # SOUL 내용이 실제 prompt에 포함되고, tone 규칙이 보존되어야 함
        assert "반말은 하지 말것" in result.assembled_text
        # 순서: SOUL → AGENT → USER → MEMORY
        assert result.assembled_text.index("SOUL") < result.assembled_text.index("AGENT")
        assert result.assembled_text.index("AGENT") < result.assembled_text.index("USER")
        assert result.assembled_text.index("USER") < result.assembled_text.index("MEMORY")
        assert result.parts[0].file_type == FileType.SOUL

    def test_missing_soul_is_graceful(self):
        """SOUL 파일이 없어도 기존 AGENT/USER/MEMORY 조합이 깨지지 않아야 한다."""
        agent = _make_persona(FileType.AGENT, "Agent text.")
        user = _make_persona(FileType.USER, "User text.")
        memory = _make_persona(FileType.MEMORY, "Memory text.")

        result = assemble_prompt([agent, user, memory], token_budget=4096)
        assert "Agent text" in result.assembled_text
        assert "User text" in result.assembled_text
        assert "Memory text" in result.assembled_text
        assert not result.was_truncated

    def test_soul_survives_truncation(self):
        """토큰 예산 초과 시 MEMORY부터 잘려도 SOUL 규칙은 보존되어야 한다."""
        soul = _make_persona(FileType.SOUL, "반말은 하지 말것.")
        agent = _make_persona(FileType.AGENT, "Agent. " * 10)
        memory = _make_persona(FileType.MEMORY, "Memory data. " * 500)

        result = assemble_prompt([soul, agent, memory], token_budget=100)
        assert result.was_truncated
        assert result.token_count <= 100
        assert "반말은 하지 말것" in result.assembled_text


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
