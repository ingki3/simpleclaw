"""MLX-LM FunctionGemma 단일 QLoRA run의 재현·예산 계약."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from simpleclaw.evaluation.functiongemma_dataset import (
    ensure_private_output_dir,
    write_private_json,
)

MODEL_REPO = "google/functiongemma-270m-it"
MODEL_REVISION = "39eccb091651513a5dfb56892d3714c1b5b8276c"
MLX_LM_PATH = Path("/opt/homebrew/bin/mlx_lm")
SEED = 42
MAX_EPOCHS = 3
MAX_STEPS = 5000
MAX_SECONDS = 2 * 60 * 60
MAX_ARTIFACT_BYTES = 10 * 1024**3


@dataclass(frozen=True)
class TrainingConfig:
    model_path: str
    data_dir: str
    adapter_path: str
    steps: int
    seed: int = SEED
    epochs: int = MAX_EPOCHS
    max_seconds: int = MAX_SECONDS
    quantization_bits: int = 4
    quantization_group_size: int = 64
    lora_layers: int = -1
    mask_prompt: bool = True
    max_artifact_bytes: int = MAX_ARTIFACT_BYTES

    def __post_init__(self) -> None:
        if not 1 <= self.steps <= MAX_STEPS:
            raise ValueError("training steps exceed hard cap")
        if not 1 <= self.epochs <= MAX_EPOCHS:
            raise ValueError("training epochs exceed hard cap")
        if not 0 < self.max_seconds <= MAX_SECONDS:
            raise ValueError("training time exceeds hard cap")
        if self.seed != SEED:
            raise ValueError("PoC permits only seed 42")
        if not 1 <= self.max_artifact_bytes <= MAX_ARTIFACT_BYTES:
            raise ValueError("training artifact size exceeds hard cap")


def preflight() -> dict[str, Any]:
    if not MLX_LM_PATH.is_file() or not os.access(MLX_LM_PATH, os.X_OK):
        raise FileNotFoundError(MLX_LM_PATH)
    result = subprocess.run(
        [str(MLX_LM_PATH), "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return {
        "mlx_lm_path": str(MLX_LM_PATH),
        "mlx_lm_version": result.stdout.strip(),
        "model_repo": MODEL_REPO,
        "model_revision": MODEL_REVISION,
    }


def artifact_size(path: str | Path) -> int:
    root = Path(path)
    return sum(item.stat().st_size for item in root.rglob("*") if item.is_file())


def _stop_process(
    process: Any,
    *,
    grace_seconds: float,
) -> None:
    process.terminate()
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_training(
    config: TrainingConfig,
    *,
    process_invocation_count: int = 1,
    process_factory: Callable[..., Any] = subprocess.Popen,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    size_reader: Callable[[str | Path], int] = artifact_size,
    poll_interval: float = 1.0,
    terminate_grace_seconds: float = 10.0,
    preflight_fn: Callable[[], dict[str, Any]] = preflight,
) -> dict[str, Any]:
    """resume을 거부하고 tracker 업로드를 끈 단일 MLX QLoRA run을 실행한다."""
    if process_invocation_count != 1:
        raise ValueError("PoC requires exactly one training process invocation")
    preflight_data = preflight_fn()
    adapter = Path(config.adapter_path)
    if adapter.exists() and any(adapter.iterdir()):
        raise FileExistsError("adapter path is non-empty; resume is forbidden")
    ensure_private_output_dir(adapter)
    command = [
        str(MLX_LM_PATH),
        "lora",
        "--model", config.model_path,
        "--train",
        "--data", config.data_dir,
        "--fine-tune-type", "lora",
        "--mask-prompt",
        "--num-layers", str(config.lora_layers),
        "--batch-size", "1",
        "--iters", str(config.steps),
        "--val-batches", "-1",
        "--learning-rate", "1e-5",
        "--steps-per-report", "1",
        "--steps-per-eval", str(max(1, min(50, config.steps))),
        "--save-every", str(config.steps),
        "--max-seq-length", "1024",
        "--adapter-path", str(adapter),
        "--seed", str(config.seed),
    ]
    environment = {
        **os.environ,
        "WANDB_DISABLED": "true",
        "WANDB_MODE": "disabled",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "MLFLOW_TRACKING_URI": "",
        "SWANLAB_MODE": "disabled",
    }
    started = clock()
    stdout_path = adapter.parent / "training-stdout.log"
    stderr_path = adapter.parent / "training-stderr.log"
    stdout_fd = os.open(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    stderr_fd = os.open(stderr_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(stdout_fd, "w", encoding="utf-8") as stdout_handle, os.fdopen(
        stderr_fd,
        "w",
        encoding="utf-8",
    ) as stderr_handle:
        process = process_factory(
            command,
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
            env=environment,
        )
        stop_reason = "completed"
        peak_size = 0
        while process.poll() is None:
            elapsed = clock() - started
            size = size_reader(adapter)
            peak_size = max(peak_size, size)
            if size >= config.max_artifact_bytes:
                stop_reason = "disk_cap"
                _stop_process(process, grace_seconds=terminate_grace_seconds)
                break
            if elapsed >= config.max_seconds:
                stop_reason = "time_cap"
                _stop_process(process, grace_seconds=terminate_grace_seconds)
                break
            sleeper(poll_interval)
        process.communicate()
    stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
    elapsed = clock() - started
    size = size_reader(adapter)
    peak_size = max(peak_size, size)
    if stop_reason == "completed":
        if size >= config.max_artifact_bytes:
            stop_reason = "disk_cap"
        elif elapsed >= config.max_seconds:
            stop_reason = "time_cap"
        elif process.returncode:
            stop_reason = "process_error"
    manifest = {
        **preflight_data,
        "config": asdict(config),
        "command": command,
        "process_invocation_count": process_invocation_count,
        "adapter_path": str(adapter.resolve()),
        "elapsed_seconds": elapsed,
        "returncode": process.returncode,
        "artifact_bytes": size,
        "peak_artifact_bytes": peak_size,
        "artifact_cap_bytes": config.max_artifact_bytes,
        "stop_reason": stop_reason,
        "consumed_budget": {
            "elapsed_seconds": elapsed,
            "steps_requested": config.steps,
            "artifact_bytes": size,
            "peak_artifact_bytes": peak_size,
        },
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-4000:],
    }
    write_private_json(adapter.parent / "training-manifest.json", manifest)
    if stop_reason != "completed":
        raise RuntimeError(f"MLX training stopped at {stop_reason}")
    if process.returncode:
        raise RuntimeError(
            f"MLX training failed with return code {process.returncode}"
        )
    if not any(adapter.iterdir()):
        raise RuntimeError("MLX training produced no adapter artifact")
    return manifest


def resolve_model_snapshot(
    output_dir: str | Path,
    *,
    allow_download: bool,
) -> Path:
    """exact revision을 resolve하고 4-bit/group64 MLX model로 변환한다."""
    output = Path(output_dir)
    if (output / "config.json").is_file():
        return output
    if not allow_download:
        raise PermissionError("model download/convert requires explicit opt-in")
    from huggingface_hub import snapshot_download

    source = snapshot_download(repo_id=MODEL_REPO, revision=MODEL_REVISION)
    ensure_private_output_dir(output.parent)
    command = [
        str(MLX_LM_PATH), "convert",
        "--hf-path", source,
        "--mlx-path", str(output),
        "--quantize",
        "--q-bits", "4",
        "--q-group-size", "64",
    ]
    subprocess.run(command, check=True, timeout=MAX_SECONDS)
    return output
