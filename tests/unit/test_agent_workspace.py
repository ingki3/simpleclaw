"""Tests for the workspace manager."""

import pytest

from simpleclaw.agents.workspace import WorkspaceManager


class TestWorkspaceManager:
    def test_create_workspace(self, tmp_path):
        manager = WorkspaceManager(tmp_path / "workspaces")
        path = manager.create("agent-123")
        assert path.exists()
        assert path.name == "agent-123"

    def test_cleanup_workspace(self, tmp_path):
        manager = WorkspaceManager(tmp_path / "workspaces")
        manager.create("agent-456")
        manager.cleanup("agent-456")
        assert not (tmp_path / "workspaces" / "agent-456").exists()

    def test_cleanup_nonexistent(self, tmp_path):
        manager = WorkspaceManager(tmp_path / "workspaces")
        manager.cleanup("nonexistent")  # Should not raise

    def test_cleanup_all(self, tmp_path):
        manager = WorkspaceManager(tmp_path / "workspaces")
        manager.create("agent-1")
        manager.create("agent-2")
        manager.create("agent-3")
        manager.cleanup_all()
        assert list((tmp_path / "workspaces").iterdir()) == []

    def test_should_cleanup_flag(self, tmp_path):
        manager = WorkspaceManager(tmp_path, cleanup=True)
        assert manager.should_cleanup is True
        manager2 = WorkspaceManager(tmp_path, cleanup=False)
        assert manager2.should_cleanup is False

    def test_base_dir(self, tmp_path):
        manager = WorkspaceManager(tmp_path / "ws")
        assert manager.base_dir == tmp_path / "ws"
