"""Workspace manager: create and cleanup sandboxed sub-agent directories."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


class WorkspaceManager:
    """Manages sandboxed workspace directories for sub-agents."""

    def __init__(
        self,
        base_dir: str | Path,
        cleanup: bool = False,
    ) -> None:
        self._base_dir = Path(base_dir)
        self._cleanup_on_complete = cleanup

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def create(self, agent_id: str) -> Path:
        """Create a workspace directory for a sub-agent."""
        workspace = self._base_dir / agent_id
        workspace.mkdir(parents=True, exist_ok=True)
        logger.info("Created workspace: %s", workspace)
        return workspace

    def cleanup(self, agent_id: str) -> None:
        """Remove a sub-agent's workspace directory."""
        workspace = self._base_dir / agent_id
        if workspace.exists():
            shutil.rmtree(workspace)
            logger.info("Cleaned up workspace: %s", workspace)

    def cleanup_all(self) -> None:
        """Remove all sub-agent workspace directories."""
        if self._base_dir.exists():
            for child in self._base_dir.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
            logger.info("Cleaned up all workspaces in: %s", self._base_dir)

    @property
    def should_cleanup(self) -> bool:
        return self._cleanup_on_complete
