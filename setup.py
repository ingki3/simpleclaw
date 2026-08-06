"""Setuptools hooks for packaging repository-authored runtime assets."""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPyWithRuntimeAssets(build_py):
    """Copy the generic runtime asset tree into the built package."""

    def run(self) -> None:
        super().run()
        source = Path("runtime_assets")
        if not source.is_dir():
            raise RuntimeError("runtime_assets source tree is missing")
        destination = Path(self.build_lib) / "simpleclaw" / "runtime_assets"
        shutil.copytree(source, destination, dirs_exist_ok=True)


setup(cmdclass={"build_py": BuildPyWithRuntimeAssets})

