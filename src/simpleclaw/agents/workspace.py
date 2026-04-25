"""워크스페이스 매니저: 서브에이전트용 샌드박스 디렉터리 생성 및 정리.

각 서브에이전트에 격리된 작업 디렉터리를 제공하여 파일 시스템 충돌을 방지한다.
cleanup 옵션에 따라 작업 완료 후 자동 삭제가 가능하다.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


class WorkspaceManager:
    """서브에이전트용 샌드박스 워크스페이스 디렉터리를 관리한다.

    base_dir 아래에 agent_id별 하위 디렉터리를 생성하고,
    cleanup_on_complete 설정에 따라 완료 후 삭제한다.
    """

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
        """서브에이전트용 워크스페이스 디렉터리를 생성한다."""
        workspace = self._base_dir / agent_id
        workspace.mkdir(parents=True, exist_ok=True)
        logger.info("Created workspace: %s", workspace)
        return workspace

    def cleanup(self, agent_id: str) -> None:
        """서브에이전트의 워크스페이스 디렉터리를 삭제한다."""
        workspace = self._base_dir / agent_id
        if workspace.exists():
            shutil.rmtree(workspace)
            logger.info("Cleaned up workspace: %s", workspace)

    def cleanup_all(self) -> None:
        """모든 서브에이전트 워크스페이스 디렉터리를 삭제한다."""
        if self._base_dir.exists():
            for child in self._base_dir.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
            logger.info("Cleaned up all workspaces in: %s", self._base_dir)

    @property
    def should_cleanup(self) -> bool:
        return self._cleanup_on_complete
