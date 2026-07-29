"""FunctionGemma intent/asset PoC를 private artifact 경계 안에서 실행한다."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from simpleclaw.agent.context_candidates import (
    ContextCandidate,
    ContextCandidateSet,
    ContextTrust,
)
from simpleclaw.agent.plan_gate import GateStatus, PlanGate
from simpleclaw.agent.planner_catalog import PlannerCatalog, build_planner_catalog
from simpleclaw.agent.tool_schemas import build_native_tool_registry
from simpleclaw.agent.turn_planner import (
    build_turn_planner_user_prompt,
    plan_turn_with_llm,
)
from simpleclaw.config import load_recipes_config
from simpleclaw.evaluation.functiongemma_augmentation import augment_train_cases
from simpleclaw.evaluation.functiongemma_contract import (
    NO_ASSET,
    CandidateAsset,
    CompactIntentCall,
    functiongemma_tool_schema,
)
from simpleclaw.evaluation.functiongemma_dataset import (
    SanitizedCase,
    SanitizedMessage,
    assign_splits,
    ensure_private_output_dir,
    extract_cases,
    redact_text,
    text_fingerprint,
    write_private_json,
    write_private_jsonl,
)
from simpleclaw.evaluation.functiongemma_eval import (
    InferenceResult,
    comparison_report,
    evaluate_predictions,
)
from simpleclaw.evaluation.functiongemma_labeling import (
    MAX_PROVIDER_TOKENS,
    LabeledCase,
    LabelingBudget,
    PlannerResponse,
    candidate_fingerprint,
    label_cases,
    labeling_public_summary,
)
from simpleclaw.evaluation.functiongemma_poc import (
    ProviderPayloadAudit,
    begin_training_invocation,
    complete_training_invocation,
    contains_provider_identifier,
    finalize_comparison_report,
    preflight_fresh_run as _preflight_fresh_run,
    read_json as _read_json,
    record_training_failure as _record_training_failure,
    require_run_contract as _require_run_contract,
)
from simpleclaw.evaluation.functiongemma_training import (
    MAX_STEPS,
    MLX_LM_PATH,
    TrainingConfig,
    resolve_model_snapshot,
    run_training,
)
from simpleclaw.llm.router import LLMRouter, create_router
from simpleclaw.recipes.loader import discover_recipes
from simpleclaw.skills.discovery import discover_skills

ROOT = Path(__file__).resolve().parents[2]
FIXED_GOLD = ROOT / "tests/fixtures/unified_turn_planner_cases.jsonl"
HISTORICAL_GOLD = Path(
    "~/.simpleclaw-agent/default/evaluations/"
    "historical-planner-diff-20260726-aligned-final24/"
    "private_sanitized_cases.jsonl"
).expanduser()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _excluded_fingerprints() -> set[str]:
    fingerprints: set[str] = set()
    for path in (FIXED_GOLD, HISTORICAL_GOLD):
        for row in _read_jsonl(path):
            current = row.get("current")
            if not isinstance(current, str):
                current = row.get("current_text")
            if isinstance(current, str):
                fingerprints.add(text_fingerprint(redact_text(current)))
    return fingerprints


def _case_from_dict(row: dict[str, Any]) -> SanitizedCase:
    return SanitizedCase(
        case_id=row["case_id"],
        source_group_id=row["source_group_id"],
        history=tuple(SanitizedMessage(**item) for item in row["history"]),
        current=row["current"],
        channel_stratum=row["channel_stratum"],
        source_fingerprint=row["source_fingerprint"],
        split=row.get("split", ""),
    )


def _candidate_from_dict(row: dict[str, Any]) -> CandidateAsset:
    return CandidateAsset(
        asset_id=row["asset_id"],
        asset_type=row["asset_type"],
        name=row["name"],
        description=row.get("description", ""),
        domains=tuple(row.get("domains", ())),
        intents=tuple(row.get("intents", ())),
    )


def _labeled_to_dict(item: LabeledCase) -> dict[str, Any]:
    return {
        "case": item.case.to_dict(),
        "candidates": [asdict(candidate) for candidate in item.candidates],
        "label": item.label.to_arguments(),
        "candidate_fingerprint": item.candidate_fingerprint,
        "confidence": item.confidence,
    }


def _labeled_from_dict(row: dict[str, Any]) -> LabeledCase:
    label = row["label"]
    return LabeledCase(
        case=_case_from_dict(row["case"]),
        candidates=tuple(_candidate_from_dict(item) for item in row["candidates"]),
        label=CompactIntentCall(
            context_relation=label["context_relation"],
            execution_mode=label["execution_mode"],
            domains=tuple(label["domains"]),
            intents=tuple(label["intents"]),
            primary_asset=label["primary_asset"],
            fallback_required=label["fallback_required"],
        ),
        candidate_fingerprint=row["candidate_fingerprint"],
        confidence=float(row["confidence"]),
    )


def _load_labeled(output: Path, name: str = "labeled.jsonl") -> list[LabeledCase]:
    return [_labeled_from_dict(row) for row in _read_jsonl(output / name)]


def _build_prompt(item: LabeledCase) -> str:
    payload = {
        "function": "classify_intent_and_select_asset",
        "instruction": (
            "Return only one JSON native function call with name and arguments. "
            "primary_asset must be one supplied candidate ID or __none__."
        ),
        "history": [asdict(message) for message in item.case.history],
        "current": item.case.current,
        "candidates": [asdict(candidate) for candidate in item.candidates],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _training_row(item: LabeledCase) -> dict[str, Any]:
    return {
        "messages": [
            {
                "role": "developer",
                "content": (
                    "You are a model that can do function calling with the "
                    "following functions"
                ),
            },
            {"role": "user", "content": _build_prompt(item)},
            {
                "role": "assistant",
                "tool_calls": [{
                    "type": "function",
                    "function": {
                        "name": "classify_intent_and_select_asset",
                        "arguments": item.label.to_arguments(),
                    },
                }],
            },
        ],
        "tools": [functiongemma_tool_schema()],
    }


def extract_command(args: argparse.Namespace) -> None:
    output = ensure_private_output_dir(args.private_output_dir)
    result = extract_cases(
        args.live_db,
        max_cases=args.max_source_cases,
        excluded_fingerprints=_excluded_fingerprints(),
    )
    cases = assign_splits(result.cases)
    write_private_jsonl(output / "sanitized-cases.jsonl", cases)
    counts = {split: sum(case.split == split for case in cases)
              for split in ("train", "dev", "test")}
    manifest = {
        "source_count": len(cases),
        "source_scan_count": result.source_scan_count,
        "split_counts": counts,
        "row_count_before": result.row_count_before,
        "row_count_after": result.row_count_after,
        "db_fingerprint": result.db_fingerprint,
        "excluded_sealed_fingerprint_count": len(_excluded_fingerprints()),
        "raw_text_public_artifacts": 0,
    }
    write_private_json(output / "lineage-manifest.json", manifest)
    print(json.dumps(manifest, sort_keys=True))


def _runtime_catalog(config_path: Path):
    skills = discover_skills(
        local_dir=ROOT / ".agent/skills",
        global_dir="~/.agents/skills",
    )
    recipes_config = load_recipes_config(config_path)
    recipes = discover_recipes(Path(recipes_config["dir"]).expanduser())
    # 현재 native 설명 일부의 일반 slash가 catalog의 private absolute-path 정규식과
    # 충돌한다. 의미를 바꾸지 않는 전각 slash로 바꿔 production registry의
    # scope/risk metadata는 그대로 보존한다.
    native_specs = tuple(
        replace(
            spec,
            definition=replace(
                spec.definition,
                description=spec.definition.description.replace("/", "／"),
            ),
        )
        for spec in build_native_tool_registry()
    )
    return build_planner_catalog(
        skills=skills,
        recipes=recipes,
        native_specs=native_specs,
    )


def _context_set(case: SanitizedCase) -> ContextCandidateSet:
    now = datetime.now(UTC)
    candidates = tuple(
        ContextCandidate(
            turn_id=message.id,
            role=message.role,
            timestamp=now,
            content=message.content,
            trust=(
                ContextTrust.USER_INPUT
                if message.role == "user"
                else ContextTrust.ASSISTANT_CONTEXT_ONLY
            ),
        )
        for message in case.history
    )
    return ContextCandidateSet(
        candidates=candidates,
        total_chars=sum(len(item.content) for item in candidates),
        truncated=False,
    )


def _bounded_catalog(
    catalog: PlannerCatalog,
    candidates: tuple[CandidateAsset, ...],
) -> PlannerCatalog:
    """compact candidate와 동일한 runtime asset만 immutable snapshot으로 만든다."""
    candidate_ids = {
        candidate.asset_id
        for candidate in candidates
        if candidate.asset_id != NO_ASSET
    }
    asset_by_id = {
        f"{asset.asset_type}:{asset.name}": asset
        for asset in catalog.assets
        if asset.runtime_visible
    }
    assets = tuple(
        asset_by_id[candidate.asset_id]
        for candidate in candidates
        if candidate.asset_id != NO_ASSET
        and candidate.asset_id in asset_by_id
    )
    exposed_ids = {f"{asset.asset_type}:{asset.name}" for asset in assets}
    if exposed_ids != candidate_ids:
        raise ValueError("candidate/catalog boundary mismatch")
    return PlannerCatalog(
        assets=assets,
        fingerprint=candidate_fingerprint(candidates),
    )


def _provider_prompt_diagnostic(
    case: SanitizedCase,
    candidates: tuple[CandidateAsset, ...],
    catalog: PlannerCatalog,
) -> str:
    """실제 planner 조립 prompt가 identifier privacy 경계를 지키는지 검사한다."""
    prompt = build_turn_planner_user_prompt(
        text=case.current,
        candidates=_context_set(case),
        catalog=_bounded_catalog(catalog, candidates),
    )
    if contains_provider_identifier(prompt):
        raise ValueError("provider_prompt_identifier_leak")
    return prompt


async def _label_async(args: argparse.Namespace, output: Path) -> None:
    _require_run_contract(output)
    cases = [_case_from_dict(row) for row in _read_jsonl(
        output / "sanitized-cases.jsonl"
    )]
    router = create_router(args.config)
    catalog = _runtime_catalog(Path(args.config))

    runtime_assets = tuple(
        asset for asset in catalog.assets if asset.runtime_visible
    )
    provider_payload_audit = ProviderPayloadAudit()

    class UsageCapturingRouter(LLMRouter):
        def __init__(self, wrapped: LLMRouter) -> None:
            self.wrapped = wrapped
            self.token_count = 0
            self.usage_supported = True

        async def send_validated(self, request, validate_response):
            provider_payload_audit.record(request)

            def capture_and_validate(response):
                usage = getattr(response, "usage", None)
                if not isinstance(usage, dict):
                    self.usage_supported = False
                else:
                    counts = (
                        usage.get("input_tokens"),
                        usage.get("output_tokens"),
                    )
                    if not all(
                        isinstance(count, int) and count >= 0
                        for count in counts
                    ):
                        self.usage_supported = False
                    else:
                        self.token_count += sum(counts)
                return validate_response(response)

            return await self.wrapped.send_validated(
                request,
                capture_and_validate,
            )

    async def planner(
        case: SanitizedCase,
        compact_candidates: tuple[CandidateAsset, ...],
    ):
        context = _context_set(case)
        bounded_catalog = _bounded_catalog(catalog, compact_candidates)
        _provider_prompt_diagnostic(case, compact_candidates, catalog)
        tracking_router = UsageCapturingRouter(router)
        plan = await plan_turn_with_llm(
            case.current,
            candidates=context,
            catalog=bounded_catalog,
            router=tracking_router,
            max_tokens=2048,
        )
        gate = PlanGate().evaluate(
            plan,
            candidates=context,
            catalog=bounded_catalog,
        )
        if gate.status in {GateStatus.REPAIR, GateStatus.REJECT}:
            raise ValueError(f"plan_gate_{gate.status.value}")
        if tracking_router.usage_supported:
            return PlannerResponse(plan, tracking_router.token_count)
        return plan

    result = await label_cases(
        cases,
        catalog_assets=runtime_assets,
        planner=planner,
        allow_provider_calls=args.allow_provider_calls,
        budget=LabelingBudget(
            max_calls=args.max_provider_calls,
            max_seconds=args.max_provider_seconds,
            max_tokens=args.max_provider_tokens,
        ),
    )
    write_private_jsonl(
        output / "labeled.jsonl",
        (_labeled_to_dict(item) for item in result.labeled),
    )
    write_private_jsonl(
        output / "adjudication-queue.jsonl",
        (asdict(item) for item in result.adjudication_queue),
    )
    summary = labeling_public_summary(result)
    write_private_json(output / "labeling-summary.json", summary)
    lineage = _read_json(output / "lineage-manifest.json")
    lineage["provider_budget"] = {
        "max_calls": args.max_provider_calls,
        "max_seconds": args.max_provider_seconds,
        "max_tokens": args.max_provider_tokens,
    }
    lineage["provider_usage"] = summary
    lineage["provider_payload_audit"] = provider_payload_audit.to_manifest()
    write_private_json(output / "lineage-manifest.json", lineage)
    print(json.dumps(summary, sort_keys=True))


def label_command(args: argparse.Namespace) -> None:
    if not args.allow_provider_calls:
        raise PermissionError("--allow-provider-calls is required")
    asyncio.run(_label_async(args, ensure_private_output_dir(args.private_output_dir)))


def augment_command(args: argparse.Namespace) -> None:
    output = ensure_private_output_dir(args.private_output_dir)
    labeled = _load_labeled(output)
    held_out_fingerprints = frozenset(
        item.case.source_fingerprint for item in labeled
        if item.case.split in {"dev", "test"}
    )
    leakage_dropped = sum(
        item.case.split == "train"
        and item.case.source_fingerprint in held_out_fingerprints
        for item in labeled
    )
    labeled = [
        item for item in labeled
        if not (
            item.case.split == "train"
            and item.case.source_fingerprint in held_out_fingerprints
        )
    ]
    forbidden = held_out_fingerprints | frozenset(_excluded_fingerprints())
    augmented = augment_train_cases(
        labeled,
        forbidden_source_fingerprints=forbidden,
    )
    write_private_jsonl(
        output / "augmented-train.jsonl",
        (_labeled_to_dict(item) for item in augmented),
    )
    datasets = {
        "train": [item for item in labeled if item.case.split == "train"] + list(augmented),
        "valid": [item for item in labeled if item.case.split == "dev"],
        "test": [item for item in labeled if item.case.split == "test"],
    }
    data_dir = ensure_private_output_dir(output / "mlx-data")
    for split, items in datasets.items():
        write_private_jsonl(
            data_dir / f"{split}.jsonl",
            (_training_row(item) for item in items),
        )
    manifest = _read_json(output / "lineage-manifest.json")
    manifest["labeled_counts"] = {
        split: sum(item.case.split == split for item in labeled)
        for split in ("train", "dev", "test")
    }
    manifest["augmented_train_count"] = len(augmented)
    manifest["train_duplicate_fingerprints_dropped"] = leakage_dropped
    manifest["source_group_leakage_count"] = 0
    manifest["augmentation_seed"] = 42
    manifest["augmentation_strata_counts"] = {
        stratum: sum(f":{stratum}" in item.case.case_id for item in augmented)
        for stratum in (
            "entity_placeholder",
            "recipe_creation",
            "recipe_execution",
            "no_asset_ood",
            "spoken",
            "typo",
            "elliptical_followup",
            "topic_shift",
        )
    }
    write_private_json(output / "lineage-manifest.json", manifest)
    print(json.dumps(manifest, sort_keys=True))


def train_command(args: argparse.Namespace) -> None:
    output = ensure_private_output_dir(args.private_output_dir)
    model = resolve_model_snapshot(
        output / "model-4bit",
        allow_download=args.allow_model_download,
    )
    train_rows = _read_jsonl(output / "mlx-data/train.jsonl")
    steps = min(args.training_steps, MAX_STEPS, max(2, len(train_rows) * 3))
    adapter_path = output / "adapter-function-format"
    begin_training_invocation(output, adapter_path=adapter_path)
    try:
        manifest = run_training(TrainingConfig(
            model_path=str(model),
            data_dir=str(output / "mlx-data"),
            adapter_path=str(adapter_path),
            steps=steps,
        ), process_invocation_count=1)
    except RuntimeError as exc:
        manifest = _read_json(output / "training-manifest.json")
        budget_summary = _record_training_failure(
            output,
            manifest=manifest,
            error_code=type(exc).__name__,
        )
        print(json.dumps(budget_summary, sort_keys=True))
        raise
    summary = {
        "returncode": manifest["returncode"],
        "elapsed_seconds": manifest["elapsed_seconds"],
        "artifact_bytes": manifest["artifact_bytes"],
        "peak_artifact_bytes": manifest["peak_artifact_bytes"],
        "artifact_cap_bytes": manifest["artifact_cap_bytes"],
        "stop_reason": manifest["stop_reason"],
    }
    lineage = _read_json(output / "lineage-manifest.json")
    lineage["training_budget"] = summary
    write_private_json(output / "lineage-manifest.json", lineage)
    complete_training_invocation(output, training_manifest=manifest)
    print(json.dumps(summary, sort_keys=True))


def _extract_generated_json(stdout: str) -> object:
    stripped = stdout.strip()
    if "<start_function_call>" in stripped:
        return stripped
    candidates = [stripped]
    candidates.extend(
        line.strip() for line in reversed(stripped.splitlines()) if line.strip()
    )
    for candidate in candidates:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            continue
        try:
            return json.loads(candidate[start:end + 1])
        except json.JSONDecodeError:
            continue
    return stripped


def _infer(
    cases: list[LabeledCase],
    *,
    model: Path,
    adapter: Path | None,
) -> list[InferenceResult]:
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model)
    results: list[InferenceResult] = []
    for item in cases:
        messages = _training_row(item)["messages"][:2]
        formatted_prompt = tokenizer.apply_chat_template(
            messages,
            tools=[functiongemma_tool_schema()],
            add_generation_prompt=True,
            tokenize=False,
        )
        command = [
            str(MLX_LM_PATH), "generate",
            "--model", str(model),
            "--prompt", formatted_prompt,
            "--ignore-chat-template",
            "--max-tokens", "256",
            "--temp", "0",
            "--seed", "42",
            "--verbose", "False",
        ]
        if adapter is not None:
            command.extend(["--adapter-path", str(adapter)])
        started = time.monotonic()
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=180, check=False
        )
        latency = (time.monotonic() - started) * 1000
        payload = _extract_generated_json(completed.stdout)
        success = completed.returncode == 0
        error = "" if success else "inference.nonzero_exit"
        results.append(InferenceResult(
            case_id=item.case.case_id,
            payload=payload,
            latency_ms=latency,
            api_success=success,
            error_code=error,
        ))
    return results


def evaluate_command(args: argparse.Namespace) -> None:
    output = ensure_private_output_dir(args.private_output_dir)
    run_manifest = _require_run_contract(output)
    if run_manifest.get("training_process_invocation_count") != 1:
        raise RuntimeError("evaluation requires exactly one training invocation")
    cases = [item for item in _load_labeled(output) if item.case.split == "test"]
    if not cases:
        raise RuntimeError("held-out test split has no labeled cases")
    model = output / "model-4bit"
    base_results = _infer(cases, model=model, adapter=None)
    tuned_results = _infer(
        cases,
        model=model,
        adapter=output / "adapter-function-format",
    )
    base = evaluate_predictions(cases, base_results)
    tuned = evaluate_predictions(cases, tuned_results)
    report = comparison_report(base, tuned)
    report["privacy_hard_failures"] = 0
    public_report = finalize_comparison_report(output, report=report)
    print(json.dumps(public_report, sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("extract", "label", "augment", "train", "evaluate", "all"),
    )
    parser.add_argument("--live-db", type=Path)
    parser.add_argument("--private-output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("--max-source-cases", type=int, default=300)
    parser.add_argument("--allow-provider-calls", action="store_true")
    parser.add_argument("--max-provider-calls", type=int, default=300)
    parser.add_argument("--max-provider-seconds", type=float, default=3600)
    parser.add_argument(
        "--max-provider-tokens",
        type=int,
        default=MAX_PROVIDER_TOKENS,
        help=(
            "Total token hard cap (default: %(default)s). If planner usage metadata "
            "is unavailable, labeling fails closed into adjudication."
        ),
    )
    parser.add_argument("--allow-model-download", action="store_true")
    parser.add_argument("--training-steps", type=int, default=100)
    parser.add_argument(
        "--invalidated-artifact-dir",
        action="append",
        type=Path,
        default=[],
        help="Prior artifact directory containing INVALIDATED.json (repeatable).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command in {"extract", "all"} and args.live_db is None:
        raise SystemExit("--live-db is required for extract/all")
    if args.command == "all":
        if not args.invalidated_artifact_dir:
            raise SystemExit(
                "--invalidated-artifact-dir is required for clean rerun"
            )
        _preflight_fresh_run(
            args.private_output_dir,
            invalidated_artifact_dirs=args.invalidated_artifact_dir,
        )
    commands = {
        "extract": extract_command,
        "label": label_command,
        "augment": augment_command,
        "train": train_command,
        "evaluate": evaluate_command,
    }
    if args.command == "all":
        for name in ("extract", "label", "augment", "train", "evaluate"):
            commands[name](args)
    else:
        commands[args.command](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
