"""Tests for the markdown persona parser."""

from pathlib import Path
import tempfile

import pytest

from simpleclaw.persona.models import FileType, SourceScope
from simpleclaw.persona.parser import parse_markdown

FIXTURES = Path(__file__).parent.parent / "fixtures"


class TestParseMarkdownNormal:
    """Test normal parsing of persona files."""

    def test_parse_agent_file(self):
        result = parse_markdown(FIXTURES / "agent.md", FileType.AGENT)
        assert result.file_type == FileType.AGENT
        assert result.source_scope == SourceScope.LOCAL
        assert len(result.sections) >= 3
        assert result.raw_content != ""

    def test_parse_user_file(self):
        result = parse_markdown(FIXTURES / "user.md", FileType.USER)
        assert result.file_type == FileType.USER
        titles = [s.title for s in result.sections]
        assert "User Profile" in titles
        assert "Basic Info" in titles

    def test_parse_memory_file(self):
        result = parse_markdown(FIXTURES / "memory.md", FileType.MEMORY)
        assert result.file_type == FileType.MEMORY
        assert any(s.title == "Core Memory" for s in result.sections)

    def test_sections_preserve_order(self):
        result = parse_markdown(FIXTURES / "agent.md", FileType.AGENT)
        titles = [s.title for s in result.sections if s.title]
        assert titles == ["Agent Identity", "Role", "Tone & Style"]

    def test_section_content_not_empty(self):
        result = parse_markdown(FIXTURES / "agent.md", FileType.AGENT)
        for section in result.sections:
            if section.title:
                assert section.content != ""


class TestParseMarkdownEdgeCases:
    """Test edge cases for the parser."""

    def test_nonexistent_file(self):
        result = parse_markdown("/nonexistent/path.md", FileType.AGENT)
        assert result.file_type == FileType.AGENT
        assert result.sections == []
        assert result.raw_content == ""

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
            f.write("")
            f.flush()
            result = parse_markdown(f.name, FileType.AGENT)
        assert result.sections == []

    def test_file_with_no_headings(self):
        with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
            f.write("Just some plain text\nwith no headings at all.")
            f.flush()
            result = parse_markdown(f.name, FileType.AGENT)
        assert len(result.sections) == 1
        assert result.sections[0].level == 0
        assert "plain text" in result.sections[0].content

    def test_nested_headings(self):
        content = "# Top\n\ntop content\n\n## Sub\n\nsub content\n\n### Deep\n\ndeep content\n"
        with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
            f.write(content)
            f.flush()
            result = parse_markdown(f.name, FileType.AGENT)
        levels = [s.level for s in result.sections]
        assert levels == [1, 2, 3]

    def test_non_utf8_file(self):
        with tempfile.NamedTemporaryFile(suffix=".md", mode="wb", delete=False) as f:
            f.write(b"\xff\xfe Invalid UTF-8 \x80\x81")
            f.flush()
            result = parse_markdown(f.name, FileType.AGENT)
        assert result.sections == []
        assert result.raw_content == ""

    def test_source_scope_global(self):
        result = parse_markdown(
            FIXTURES / "agent.md", FileType.AGENT, SourceScope.GLOBAL
        )
        assert result.source_scope == SourceScope.GLOBAL
