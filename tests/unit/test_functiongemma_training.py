"""BIZ-514 학습 child process의 실행 중 hard-cap 계약."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from simpleclaw.evaluation.functiongemma_training import (
    TrainingConfig,
    run_training,
)


class _Process:
    def __init__(self, *, finish_after_polls: int | None = None) -> None:
        self.finish_after_polls = finish_after_polls
        self.polls = 0
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self):
        self.polls += 1
        if (
            self.finish_after_polls is not None
            and self.polls >= self.finish_after_polls
        ):
            self.returncode = 0
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout=None):
        self.returncode = -15
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def communicate(self):
        return "", ""


def _config(tmp_path: Path, **changes) -> TrainingConfig:
    values = {
        "model_path": "model",
        "data_dir": "data",
        "adapter_path": str(tmp_path / "adapter"),
        "steps": 2,
        "max_artifact_bytes": 10,
    }
    values.update(changes)
    return TrainingConfig(**values)


def _preflight():
    return {"mlx_lm_version": "test"}


def test_disk_cap_terminates_running_process_and_writes_manifest(
    tmp_path: Path,
) -> None:
    process = _Process()
    sizes = iter((1, 10, 10))

    with pytest.raises(RuntimeError, match="disk_cap"):
        run_training(
            _config(tmp_path),
            process_factory=lambda *args, **kwargs: process,
            size_reader=lambda path: next(sizes),
            sleeper=lambda _: None,
            preflight_fn=_preflight,
        )

    assert process.terminated
    manifest = (
        tmp_path / "training-manifest.json"
    ).read_text(encoding="utf-8")
    assert '"stop_reason": "disk_cap"' in manifest
    assert '"peak_artifact_bytes": 10' in manifest


def test_normal_completion_does_not_terminate(tmp_path: Path) -> None:
    process = _Process(finish_after_polls=2)
    adapter = tmp_path / "adapter"

    def size(path):
        (adapter / "adapter.safetensors").write_bytes(b"x")
        return 1

    result = run_training(
        _config(tmp_path),
        process_factory=lambda *args, **kwargs: process,
        size_reader=size,
        sleeper=lambda _: None,
        preflight_fn=_preflight,
    )
    assert result["stop_reason"] == "completed"
    assert not process.terminated


@pytest.mark.parametrize(
    ("finish_after_polls", "sizes"),
    [
        (1, (10,)),
        (2, (1, 11)),
    ],
)
def test_final_size_at_or_above_cap_fails_closed_after_child_exit(
    tmp_path: Path,
    finish_after_polls: int,
    sizes: tuple[int, ...],
) -> None:
    process = _Process(finish_after_polls=finish_after_polls)
    observed_sizes = iter(sizes)

    with pytest.raises(RuntimeError, match="disk_cap"):
        run_training(
            _config(tmp_path),
            process_factory=lambda *args, **kwargs: process,
            size_reader=lambda path: next(observed_sizes),
            sleeper=lambda _: None,
            preflight_fn=_preflight,
        )

    assert not process.terminated
    manifest = (
        tmp_path / "training-manifest.json"
    ).read_text(encoding="utf-8")
    assert '"stop_reason": "disk_cap"' in manifest
    assert f'"artifact_bytes": {sizes[-1]}' in manifest


def test_final_elapsed_time_at_cap_fails_closed_after_child_exit(
    tmp_path: Path,
) -> None:
    process = _Process(finish_after_polls=1)
    times = iter((0.0, 1.0))

    with pytest.raises(RuntimeError, match="time_cap"):
        run_training(
            _config(tmp_path, max_seconds=1),
            process_factory=lambda *args, **kwargs: process,
            clock=lambda: next(times),
            size_reader=lambda path: 0,
            sleeper=lambda _: None,
            preflight_fn=_preflight,
        )

    manifest = (
        tmp_path / "training-manifest.json"
    ).read_text(encoding="utf-8")
    assert '"stop_reason": "time_cap"' in manifest


def test_timeout_uses_kill_after_terminate_grace(tmp_path: Path) -> None:
    class StubbornProcess(_Process):
        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired("mlx", timeout)
            return self.returncode

    process = StubbornProcess()
    times = iter((0.0, 2.0, 2.0))
    with pytest.raises(RuntimeError, match="time_cap"):
        run_training(
            _config(tmp_path, max_seconds=1),
            process_factory=lambda *args, **kwargs: process,
            clock=lambda: next(times),
            size_reader=lambda path: 0,
            sleeper=lambda _: None,
            preflight_fn=_preflight,
        )
    assert process.terminated
    assert process.killed
