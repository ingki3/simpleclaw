"""Tests for the persona file resolver."""


import pytest

from simpleclaw.persona.models import FileType, SourceScope
from simpleclaw.persona.resolver import resolve_persona_files


@pytest.fixture
def local_dir(tmp_path):
    """Create a local persona directory with files."""
    d = tmp_path / "local"
    d.mkdir()
    (d / "AGENT.md").write_text("# Agent\n\nLocal agent content.", encoding="utf-8")
    (d / "USER.md").write_text("# User\n\nLocal user content.", encoding="utf-8")
    (d / "MEMORY.md").write_text("# Memory\n\nLocal memory.", encoding="utf-8")
    return d


@pytest.fixture
def global_dir(tmp_path):
    """Create a global persona directory with files."""
    d = tmp_path / "global"
    d.mkdir()
    (d / "AGENT.md").write_text("# Agent\n\nGlobal agent content.", encoding="utf-8")
    (d / "USER.md").write_text("# User\n\nGlobal user content.", encoding="utf-8")
    (d / "MEMORY.md").write_text("# Memory\n\nGlobal memory.", encoding="utf-8")
    return d


class TestResolverLocalOnly:
    def test_all_local_files(self, local_dir, tmp_path):
        empty_global = tmp_path / "empty_global"
        result = resolve_persona_files(local_dir, empty_global)
        assert len(result) == 3
        assert all(pf.source_scope == SourceScope.LOCAL for pf in result)

    def test_file_types_order(self, local_dir, tmp_path):
        empty_global = tmp_path / "empty_global"
        result = resolve_persona_files(local_dir, empty_global)
        types = [pf.file_type for pf in result]
        assert types == [FileType.AGENT, FileType.USER, FileType.MEMORY]


class TestResolverGlobalOnly:
    def test_all_global_files(self, global_dir, tmp_path):
        empty_local = tmp_path / "empty_local"
        result = resolve_persona_files(empty_local, global_dir)
        assert len(result) == 3
        assert all(pf.source_scope == SourceScope.GLOBAL for pf in result)


class TestResolverLocalOverride:
    def test_local_overrides_global(self, local_dir, global_dir):
        result = resolve_persona_files(local_dir, global_dir)
        assert len(result) == 3
        for pf in result:
            assert pf.source_scope == SourceScope.LOCAL
            assert "Local" in pf.raw_content


class TestResolverMixed:
    def test_mixed_local_and_global(self, tmp_path):
        local = tmp_path / "local"
        local.mkdir()
        (local / "AGENT.md").write_text("# Agent\n\nLocal agent.", encoding="utf-8")

        global_d = tmp_path / "global"
        global_d.mkdir()
        (global_d / "USER.md").write_text("# User\n\nGlobal user.", encoding="utf-8")
        (global_d / "MEMORY.md").write_text("# Memory\n\nGlobal mem.", encoding="utf-8")

        result = resolve_persona_files(local, global_d)
        assert len(result) == 3

        by_type = {pf.file_type: pf for pf in result}
        assert by_type[FileType.AGENT].source_scope == SourceScope.LOCAL
        assert by_type[FileType.USER].source_scope == SourceScope.GLOBAL
        assert by_type[FileType.MEMORY].source_scope == SourceScope.GLOBAL


class TestResolverSoul:
    """BIZ-451: SOUL.md 탐색과 정규 순서를 검증한다."""

    def test_soul_resolved_first(self, local_dir, tmp_path):
        (local_dir / "SOUL.md").write_text(
            "# Soul\n\n사용자를 형님이라고 부를 것. 반말은 하지 말것.",
            encoding="utf-8",
        )
        empty_global = tmp_path / "empty_global"
        result = resolve_persona_files(local_dir, empty_global)
        assert len(result) == 4
        types = [pf.file_type for pf in result]
        assert types == [
            FileType.SOUL,
            FileType.AGENT,
            FileType.USER,
            FileType.MEMORY,
        ]
        assert "반말은 하지 말것" in result[0].raw_content

    def test_missing_soul_skipped_with_warning(self, local_dir, tmp_path, caplog):
        """SOUL.md가 없으면 warning 로그 후 나머지 3개 파일만 반환한다."""
        empty_global = tmp_path / "empty_global"
        with caplog.at_level("WARNING"):
            result = resolve_persona_files(local_dir, empty_global)
        assert len(result) == 3
        assert FileType.SOUL not in {pf.file_type for pf in result}
        assert any("soul" in rec.message.lower() or "soul" in str(rec.args).lower()
                   for rec in caplog.records)


class TestResolverEmpty:
    def test_both_missing(self, tmp_path):
        result = resolve_persona_files(
            tmp_path / "no_local", tmp_path / "no_global"
        )
        assert result == []
