"""End-to-end integration test for the persona pipeline."""


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


class TestSoulPipeline:
    """BIZ-451: live-like persona dir(SOUL 포함)의 resolve → assemble 통합 검증."""

    @pytest.fixture
    def workspace_with_soul(self, workspace):
        (workspace["local"] / "SOUL.md").write_text(
            "# Soul\n\n"
            "## 말투\n\n"
            "- 사용자를 \"형님\"이라고 부를 것\n"
            "- 친근한 말투를 사용하되 반말은 하지 말것\n",
            encoding="utf-8",
        )
        return workspace

    def test_soul_rules_in_assembled_prompt(self, workspace_with_soul):
        """SOUL의 tone 규칙이 system prompt에 실제로 포함되어야 한다."""
        files = resolve_persona_files(
            workspace_with_soul["local"], workspace_with_soul["global"]
        )
        assert len(files) == 4

        result = assemble_prompt(files, token_budget=4096)
        text = result.assembled_text
        assert "반말은 하지 말것" in text
        assert "형님" in text
        # SOUL이 AGENT보다 앞에 주입되어야 tone guard가 안정적으로 작동
        assert text.index("반말은 하지 말것") < text.index("Agent Identity")
        assert text.index("Agent Identity") < text.index("User Profile")
