"""저장소가 관리하는 runtime asset을 package에 포함하는 build hook이다."""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPyWithRuntimeAssets(build_py):
    """generic runtime asset 트리를 build package에 복사한다."""

    def run(self) -> None:
        """기본 Python build 후 검증된 authoring tree를 package에 추가한다."""
        super().run()
        source = Path("runtime_assets")
        if not source.is_dir():
            raise RuntimeError("runtime_assets source tree is missing")
        destination = Path(self.build_lib) / "simpleclaw" / "runtime_assets"
        shutil.copytree(source, destination, dirs_exist_ok=True)


setup(cmdclass={"build_py": BuildPyWithRuntimeAssets})
