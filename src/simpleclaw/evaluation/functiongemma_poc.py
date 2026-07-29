"""FunctionGemma PoC 실행 계약·감사·보고 오케스트레이션.

CLI와 분리된 이 모듈은 reviewed prerequisite, task-owned source hash,
provider payload audit, 단일 학습 ledger, 실패 분류와 report fingerprint를
재사용 가능한 경계로 제공한다.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from simpleclaw.evaluation.functiongemma_dataset import (
    ensure_private_output_dir,
    write_private_json,
)
from simpleclaw.evaluation.functiongemma_eval import (
    canonical_json_sha256,
    file_sha256,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SOURCE_LOCK_PATH = Path(__file__).with_name(
    "functiongemma_poc_source.lock.json"
)
RUN_CONTRACT_VERSION = "functiongemma-intent-poc/biz-515-v2"
PREREQUISITE_PR_NUMBER = 527
PREREQUISITE_MERGE_SHA = "b1c659b5821fe45368596e92a8d67464503e7fd6"
MODEL_REVISION = "39eccb091651513a5dfb56892d3714c1b5b8276c"
_CAP_STOP_REASONS = frozenset({"disk_cap", "time_cap"})
_PROVIDER_IDENTIFIER_PATTERNS = (
    re.compile(r"\b(?:msg|live):\d+\b", re.IGNORECASE),
    re.compile(
        r"""(?ix)
        (?:"|'|`)?\b(?:user|chat|message|msg)[_\s-]?id\b(?:"|'|`)?
        \s*(?::|=)\s*(?:"|'|`)?[A-Za-z0-9_-]+
        """
    ),
)


def read_json(path: Path) -> Any:
    """private JSON을 UTF-8로 읽어 구조화 값으로 반환한다."""
    return json.loads(path.read_text(encoding="utf-8"))


def _load_source_lock(path: Path = SOURCE_LOCK_PATH) -> dict[str, Any]:
    """reviewed source lock을 읽고 최소 스키마를 fail-closed 검증한다."""
    payload = read_json(path)
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, dict) or not files:
        raise RuntimeError("FunctionGemma source lock has no files")
    for name, digest in files.items():
        if (
            not isinstance(name, str)
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise RuntimeError("invalid FunctionGemma source lock entry")
    return payload


SOURCE_LOCK = _load_source_lock()
RUN_CONTRACT = {
    "reviewed_prerequisite": {
        "pull_request": PREREQUISITE_PR_NUMBER,
        "merge_sha": PREREQUISITE_MERGE_SHA,
    },
    "task_owned_source": {
        "algorithm": "SHA-256",
        "canonicalization": "exact file byte sequence",
        "files": SOURCE_LOCK["files"],
    },
    "model_revision": MODEL_REVISION,
    "single_training_process": True,
    "provider_payload_audit": "router-request-canonical-json-sha256",
}
RUN_CONTRACT_FINGERPRINT = canonical_json_sha256(RUN_CONTRACT)


def _git_output(repository_root: Path, *args: str) -> str:
    """현재 checkout의 Git 값을 오류 은폐 없이 조회한다."""
    completed = subprocess.run(
        ["git", *args],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def verify_current_source(
    repository_root: Path = REPOSITORY_ROOT,
    *,
    source_lock: dict[str, Any] | None = None,
    prerequisite_merge_sha: str = PREREQUISITE_MERGE_SHA,
) -> dict[str, Any]:
    """현재 checkout이 reviewed prerequisite와 source lock에 맞는지 검증한다."""
    root = repository_root.resolve()
    lock = SOURCE_LOCK if source_lock is None else source_lock
    expected_files = lock.get("files") if isinstance(lock, dict) else None
    if not isinstance(expected_files, dict) or not expected_files:
        raise RuntimeError("FunctionGemma source lock has no files")

    actual_files: dict[str, str] = {}
    for relative_name, expected_digest in sorted(expected_files.items()):
        source_path = (root / relative_name).resolve()
        if root not in source_path.parents or not source_path.is_file():
            raise RuntimeError(
                f"missing task-owned source: {relative_name}"
            )
        actual_digest = file_sha256(source_path)
        if actual_digest != expected_digest:
            raise RuntimeError(
                f"task-owned source hash mismatch: {relative_name}"
            )
        actual_files[relative_name] = actual_digest

    source_lock_relative = SOURCE_LOCK_PATH.resolve().relative_to(root).as_posix()
    tracked_paths = [*sorted(expected_files), source_lock_relative]
    try:
        for relative_name in tracked_paths:
            subprocess.run(
                ["git", "ls-files", "--error-unmatch", relative_name],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
        subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", *tracked_paths],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "task-owned source and source lock must be committed in HEAD"
        ) from exc

    try:
        subprocess.run(
            [
                "git",
                "merge-base",
                "--is-ancestor",
                prerequisite_merge_sha,
                "HEAD",
            ],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "reviewed prerequisite merge SHA is not an ancestor of HEAD"
        ) from exc

    return {
        "status": "verified",
        "prerequisite_pull_request": PREREQUISITE_PR_NUMBER,
        "prerequisite_merge_sha": prerequisite_merge_sha,
        "checkout_commit_sha": _git_output(root, "rev-parse", "HEAD^{commit}"),
        "checkout_tree_sha": _git_output(root, "rev-parse", "HEAD^{tree}"),
        "task_owned_source_matches_checkout_commit": True,
        "task_owned_source_algorithm": "SHA-256",
        "task_owned_source_canonicalization": "exact file byte sequence",
        "task_owned_source_hashes": actual_files,
        "task_owned_source_set_fingerprint": canonical_json_sha256(actual_files),
    }


def preflight_fresh_run(
    output: Path,
    *,
    invalidated_artifact_dirs: list[Path],
    repository_root: Path = REPOSITORY_ROOT,
) -> Path:
    """fresh artifact와 reviewed source lineage를 실행 전에 고정한다."""
    source_provenance = verify_current_source(repository_root)
    resolved = output.expanduser().resolve()
    if resolved.exists() and any(resolved.iterdir()):
        raise FileExistsError("fresh private output directory must be empty")

    invalidated = []
    for artifact_dir in invalidated_artifact_dirs:
        artifact = artifact_dir.expanduser().resolve()
        if artifact == resolved:
            raise ValueError("fresh output cannot be an invalidated artifact")
        marker = artifact / "INVALIDATED.json"
        if not marker.is_file():
            raise FileNotFoundError(f"missing invalidation marker: {marker}")
        marker_payload = read_json(marker)
        if marker_payload.get("status") != "invalidated":
            raise ValueError(f"invalid invalidation marker: {marker}")
        invalidated.append({
            "directory_name": artifact.name,
            "marker_file_sha256": file_sha256(marker),
        })

    ensure_private_output_dir(resolved)
    write_private_json(resolved / "run-manifest.json", {
        "run_contract_version": RUN_CONTRACT_VERSION,
        "run_contract_fingerprint": RUN_CONTRACT_FINGERPRINT,
        "run_contract": RUN_CONTRACT,
        "execution_source_provenance": source_provenance,
        "invalidated_artifacts_excluded": invalidated,
        "training_process_invocation_count": 0,
        "training_adapter_path": None,
        "status": "initialized",
    })
    return resolved


def require_run_contract(
    output: Path,
    *,
    repository_root: Path = REPOSITORY_ROOT,
) -> dict[str, Any]:
    """artifact 계약과 현재 checkout을 함께 대조해 mismatch를 차단한다."""
    manifest_path = output / "run-manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("missing BIZ-515 run manifest")
    manifest = read_json(manifest_path)
    if (
        manifest.get("run_contract_version") != RUN_CONTRACT_VERSION
        or manifest.get("run_contract_fingerprint")
        != RUN_CONTRACT_FINGERPRINT
        or manifest.get("run_contract") != RUN_CONTRACT
    ):
        raise RuntimeError("reviewed FunctionGemma contract mismatch")

    current = verify_current_source(repository_root)
    recorded = manifest.get("execution_source_provenance")
    if not isinstance(recorded, dict):
        raise TypeError("missing execution source provenance")
    if (
        recorded.get("prerequisite_merge_sha") != PREREQUISITE_MERGE_SHA
        or recorded.get("task_owned_source_hashes")
        != current["task_owned_source_hashes"]
        or recorded.get("task_owned_source_set_fingerprint")
        != current["task_owned_source_set_fingerprint"]
    ):
        raise RuntimeError("execution source provenance mismatch")
    return manifest


def request_payload(request: Any) -> dict[str, Any]:
    """provider-neutral LLM request 필드만 canonical audit payload로 만든다."""
    schema = request.response_schema
    if not isinstance(schema, dict):
        schema = None
    return {
        "system_prompt": request.system_prompt,
        "user_message": request.user_message,
        "route_name": request.route_name,
        "backend_name": request.backend_name,
        "max_tokens": request.max_tokens,
        "response_mime_type": request.response_mime_type,
        "response_schema": schema,
        "require_structured_output": request.require_structured_output,
        "reasoning": request.reasoning,
        "required_capabilities": sorted(request.required_capabilities),
    }


def contains_provider_identifier(rendered: str) -> bool:
    """provider payload에 raw DB/user/chat/message 식별자가 있는지 검사한다."""
    return any(pattern.search(rendered) for pattern in _PROVIDER_IDENTIFIER_PATTERNS)


class ProviderPayloadAudit:
    """원문을 보존하지 않고 실제 provider payload fingerprint를 집계한다."""

    def __init__(self) -> None:
        self._fingerprints: list[str] = []

    def record(self, request: Any) -> None:
        """실제 request를 privacy 검사하고 canonical SHA-256만 보존한다."""
        payload = request_payload(request)
        rendered = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if contains_provider_identifier(rendered):
            raise ValueError("provider_payload_identifier_leak")
        self._fingerprints.append(
            hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        )

    def to_manifest(
        self,
        *,
        accepted_target_out_of_pre_call_set_count: int = 0,
    ) -> dict[str, Any]:
        """payload 원문 없이 count·개별/집합 fingerprint를 반환한다."""
        return {
            "payload_count": len(self._fingerprints),
            "accepted_target_out_of_pre_call_set_count": (
                accepted_target_out_of_pre_call_set_count
            ),
            "raw_identifier_match_count": 0,
            "fingerprint_algorithm": "SHA-256",
            "canonicalization": (
                "UTF-8 JSON; sort_keys=true; separators=(',', ':'); "
                "provider-neutral LLMRequest fields"
            ),
            "payload_set_fingerprint": canonical_json_sha256(
                self._fingerprints
            ),
            "payload_fingerprints": list(self._fingerprints),
        }


def begin_training_invocation(
    output: Path,
    *,
    adapter_path: Path,
) -> dict[str, Any]:
    """단일 학습 process ledger를 호출 직전에 원자적으로 1로 고정한다."""
    manifest = require_run_contract(output)
    invocation_count = int(
        manifest.get("training_process_invocation_count", 0)
    )
    if invocation_count != 0:
        raise RuntimeError("training process was already invoked for this run")
    manifest["training_process_invocation_count"] = 1
    manifest["training_adapter_path"] = str(adapter_path.resolve())
    manifest["status"] = "training_invoked"
    write_private_json(output / "run-manifest.json", manifest)
    return manifest


def complete_training_invocation(
    output: Path,
    *,
    training_manifest: dict[str, Any],
) -> None:
    """성공한 단일 학습의 stop reason과 소비 예산을 ledger에 반영한다."""
    run_manifest = require_run_contract(output)
    run_manifest["status"] = "training_completed"
    run_manifest["training_stop_reason"] = training_manifest["stop_reason"]
    run_manifest["training_consumed_budget"] = training_manifest[
        "consumed_budget"
    ]
    write_private_json(output / "run-manifest.json", run_manifest)


def classify_training_failure(manifest: dict[str, Any]) -> str:
    """cap 종료를 보존하고 자연 nonzero/기타 실패만 process_error로 분류한다."""
    stop_reason = manifest.get("stop_reason")
    if stop_reason in _CAP_STOP_REASONS:
        return str(stop_reason)
    return "process_error"


def _training_budget_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    """공개 가능한 학습 예산·종료 필드만 추출한다."""
    return {
        key: manifest.get(key)
        for key in (
            "stop_reason",
            "returncode",
            "elapsed_seconds",
            "artifact_bytes",
            "peak_artifact_bytes",
            "artifact_cap_bytes",
            "process_invocation_count",
            "adapter_path",
            "consumed_budget",
        )
    }


def write_fingerprinted_report(
    output: Path,
    *,
    private_filename: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], Path]:
    """private payload와 canonical/file-byte SHA-256이 분리된 report를 쓴다."""
    private_report_path = write_private_json(
        output / private_filename,
        payload,
    )
    public_report = {
        **payload,
        "report_fingerprints": {
            "canonical_payload": {
                "algorithm": "SHA-256",
                "canonicalization": (
                    "UTF-8 JSON; ensure_ascii=false; sort_keys=true; "
                    "separators=(',', ':')"
                ),
                "value": canonical_json_sha256(payload),
            },
            "private_report_file_bytes": {
                "algorithm": "SHA-256",
                "canonicalization": "none; exact file byte sequence",
                "value": file_sha256(private_report_path),
            },
        },
    }
    write_private_json(output / "aggregate-report.json", public_report)
    return public_report, private_report_path


def record_training_failure(
    output: Path,
    *,
    manifest: dict[str, Any],
    error_code: str,
) -> dict[str, Any]:
    """실패 원인을 보존해 real report boundary와 run ledger를 함께 갱신한다."""
    run_manifest = require_run_contract(output)
    failure_reason = classify_training_failure(manifest)
    manifest["stop_reason"] = failure_reason
    write_private_json(output / "training-manifest.json", manifest)
    budget_summary = _training_budget_summary(manifest)

    lineage = read_json(output / "lineage-manifest.json")
    lineage["training_budget"] = budget_summary
    write_private_json(output / "lineage-manifest.json", lineage)
    payload_audit = lineage.get("provider_payload_audit", {})
    failure_report = {
        "status": "hard_failure",
        "hard_failures": {f"training.{failure_reason}": 1},
        "training_budget": budget_summary,
        "provider_usage": lineage.get("provider_usage", {}),
        "provider_payload_audit": {
            "payload_count": payload_audit.get("payload_count", 0),
            "accepted_target_out_of_pre_call_set_count": payload_audit.get(
                "accepted_target_out_of_pre_call_set_count",
                0,
            ),
            "raw_identifier_match_count": payload_audit.get(
                "raw_identifier_match_count",
                0,
            ),
            "payload_set_fingerprint": payload_audit.get(
                "payload_set_fingerprint"
            ),
            "fingerprint_algorithm": payload_audit.get(
                "fingerprint_algorithm"
            ),
            "canonicalization": payload_audit.get("canonicalization"),
        },
        "execution_source_provenance": run_manifest[
            "execution_source_provenance"
        ],
        "evaluation_skipped": True,
        "evaluation_skip_reason": "training_adapter_unavailable",
        "recommend_shadow_integration": False,
        "raw_text_rows": 0,
        "error_code": error_code,
    }
    public_report, _ = write_fingerprinted_report(
        output,
        private_filename="private-hard-failure-report.json",
        payload=failure_report,
    )
    run_manifest["status"] = "training_failed"
    run_manifest["training_stop_reason"] = failure_reason
    run_manifest["training_consumed_budget"] = manifest.get("consumed_budget")
    run_manifest["hard_failure_report_canonical_payload_sha256"] = (
        canonical_json_sha256(public_report)
    )
    write_private_json(output / "run-manifest.json", run_manifest)
    return budget_summary


def finalize_comparison_report(
    output: Path,
    *,
    report: dict[str, Any],
) -> dict[str, Any]:
    """성공 평가 report에 예산·사용량·source provenance와 hash를 결합한다."""
    run_manifest = require_run_contract(output)
    payload = {**report, "raw_text_rows": 0}
    training_manifest_path = output / "training-manifest.json"
    if training_manifest_path.is_file():
        training = read_json(training_manifest_path)
        payload["training_budget"] = {
            "stop_reason": training.get("stop_reason"),
            "elapsed_seconds": training.get("elapsed_seconds"),
            "artifact_bytes": training.get("artifact_bytes"),
            "peak_artifact_bytes": training.get("peak_artifact_bytes"),
            "artifact_cap_bytes": training.get("artifact_cap_bytes"),
        }
    labeling_summary_path = output / "labeling-summary.json"
    if labeling_summary_path.is_file():
        payload["provider_usage"] = read_json(labeling_summary_path)
    payload["execution_source_provenance"] = run_manifest[
        "execution_source_provenance"
    ]
    public_report, _ = write_fingerprinted_report(
        output,
        private_filename="private-comparison-report.json",
        payload=payload,
    )
    run_manifest["status"] = "completed"
    run_manifest["aggregate_report_canonical_payload_sha256"] = (
        canonical_json_sha256(public_report)
    )
    write_private_json(output / "run-manifest.json", run_manifest)
    return public_report


def record_unverifiable_execution_provenance(
    output: Path,
    *,
    checkout_base_commit_sha: str,
    first_persisted_post_run_commit_sha: str,
    evidence: list[str],
) -> dict[str, Any]:
    """기존 비용 run의 복원 불가능한 exact tree를 hard-failure로 박제한다."""
    provenance = {
        "status": "unverifiable",
        "prerequisite_pull_request": PREREQUISITE_PR_NUMBER,
        "prerequisite_merge_sha": PREREQUISITE_MERGE_SHA,
        "checkout_base_commit_sha": checkout_base_commit_sha,
        "checkout_commit_sha": None,
        "checkout_tree_sha": None,
        "task_owned_source_hashes": None,
        "first_persisted_post_run_commit_sha": (
            first_persisted_post_run_commit_sha
        ),
        "hard_failure_key": "lineage.execution_source_unverifiable",
        "limitation": (
            "The paid run executed from an uncommitted worktree. Later runner "
            "and report changes were persisted only after execution, so the "
            "exact execution tree or patch hash cannot be reconstructed."
        ),
        "evidence": evidence,
    }
    lineage_path = output / "lineage-manifest.json"
    lineage = read_json(lineage_path)
    lineage["execution_source_provenance"] = provenance
    write_private_json(lineage_path, lineage)

    private_report_path = output / "private-hard-failure-report.json"
    private_report = read_json(private_report_path)
    hard_failures = dict(private_report.get("hard_failures", {}))
    hard_failures["lineage.execution_source_unverifiable"] = 1
    private_report["hard_failures"] = hard_failures
    private_report["execution_source_provenance"] = provenance
    private_report["recommend_shadow_integration"] = False
    public_report, rewritten_private_path = write_fingerprinted_report(
        output,
        private_filename=private_report_path.name,
        payload=private_report,
    )

    run_manifest_path = output / "run-manifest.json"
    run_manifest = read_json(run_manifest_path)
    run_manifest["contract_provenance_status"] = "unverifiable"
    run_manifest["execution_source_provenance"] = provenance
    run_manifest["hard_failure_report_canonical_payload_sha256"] = (
        canonical_json_sha256(public_report)
    )
    run_manifest["private_hard_failure_report_file_sha256"] = file_sha256(
        rewritten_private_path
    )
    write_private_json(run_manifest_path, run_manifest)
    return {
        "execution_source_provenance": provenance,
        "aggregate_report_canonical_payload_sha256": canonical_json_sha256(
            public_report
        ),
        "private_hard_failure_report_file_sha256": file_sha256(
            rewritten_private_path
        ),
    }
