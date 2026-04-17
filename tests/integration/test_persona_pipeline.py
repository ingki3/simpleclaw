"""End-to-end integration test for the persona pipeline."""

from pathlib import Path

import pytest

from simpleclaw.persona.resolver import resolve_persona_files
from simpleclaw.persona.assembler import assemble_prompt


@pytest.fixture
def workspace(tmp_path):
    """Create a workspace with local and global persona files."""
    local = tmp_path / ".agent"
    local.mkdir()

    (local / "AGENT.md").write_text(
        "# Agent Identity\n\n"
        "나는 SimpleClaw 에이전트입니다.\n\n"
        "## Role\n\n"
        "사용자의 개인 비서 역할을 수행합니다.\n",
        encoding="utf-8",
    )
    (local / "USER.md").write_text(
        "# User Profile\n\n"
        "## Preferences\n\n"
        "- 한국어 사용\n- 간결한 응답 선호\n",
        encoding="utf-8",
    )
    (local / "MEMORY.md").write_text(
        "# Core Memory\n\n"
        "## Recent\n\n"
        "2026-04-15: 프로젝트 초기 설정 완료.\n\n"
        "## Notes\n\n"
        "- 사용자는 Vim을 선호함\n",
        encoding="utf-8",
    )

    global_d = tmp_path / "global_agents"
    global_d.mkdir()
    # Global AGENT.md should be overridden by local
    (global_d / "AGENT.md").write_text(
        "# Agent\n\nGlobal fallback agent.\n", encoding="utf-8"
    )

    return {"local": local, "global": global_d}


class TestFullPipeline:
    def test_resolve_parse_assemble(self, workspace):
        """Full pipeline: resolve -> parse -> assemble."""
        files = resolve_persona_files(workspace["local"], workspace["global"])

        assert len(files) == 3

        result = assemble_prompt(files, token_budget=4096)

        assert result.token_count > 0
        assert result.token_count <= 4096
        assert not result.was_truncated

        # Verify order: AGENT before USER before MEMORY
        text = result.assembled_text
        assert text.index("Agent Identity") < text.index("User Profile")
        assert text.index("User Profile") < text.index("Core Memory")

    def test_local_overrides_global(self, workspace):
        """Local AGENT.md should override global."""
        files = resolve_persona_files(workspace["local"], workspace["global"])
        agent = next(f for f in files if f.file_type.value == "agent")
        assert "SimpleClaw" in agent.raw_content
        assert "Global fallback" not in agent.raw_content

    def test_truncation_with_small_budget(self, workspace):
        """Small token budget triggers truncation."""
        files = resolve_persona_files(workspace["local"], workspace["global"])
        result = assemble_prompt(files, token_budget=30)

        assert result.was_truncated
        assert result.token_count <= 30

    def test_missing_files_graceful(self, tmp_path):
        """Empty directories produce empty prompt."""
        files = resolve_persona_files(
            tmp_path / "no_local", tmp_path / "no_global"
        )
        result = assemble_prompt(files, token_budget=4096)
        assert result.assembled_text == ""
        assert result.token_count == 0
