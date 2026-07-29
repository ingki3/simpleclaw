"""BIZ-512 weak-label budget·queue·no-provider-default 계약."""

from __future__ import annotations

import asyncio
import json
from argparse import Namespace
from unittest.mock import AsyncMock

import pytest

import scripts.dev.run_functiongemma_intent_poc as intent_poc
import simpleclaw.evaluation.functiongemma_poc as functiongemma_poc
from scripts.dev.run_functiongemma_intent_poc import (
    _bounded_catalog,
    _parser,
    _provider_prompt_diagnostic,
)
from simpleclaw.agent.planner_catalog import PlannerAsset, PlannerCatalog
from simpleclaw.agent.turn_plan import (
    AssetRef,
    ClarificationPlan,
    ContextRelation,
    ContextSelection,
    EvidenceOwner,
    ExecutionMode,
    ExecutionPlan,
    FactCheckPlan,
    UnifiedTurnPlan,
)
from simpleclaw.evaluation.functiongemma_contract import (
    NO_ASSET,
    CandidateAsset,
)
from simpleclaw.evaluation.functiongemma_dataset import (
    SanitizedCase,
    SanitizedMessage,
)
from simpleclaw.evaluation.functiongemma_eval import canonical_json_sha256
from simpleclaw.evaluation.functiongemma_labeling import (
    MAX_PROVIDER_TOKENS,
    LabelingBudget,
    PlannerResponse,
    candidate_fingerprint,
    label_cases,
    labeling_public_summary,
)
from simpleclaw.evaluation.functiongemma_poc import (
    PREREQUISITE_MERGE_SHA,
    RUN_CONTRACT,
    RUN_CONTRACT_FINGERPRINT,
    preflight_fresh_run,
    record_training_failure,
    record_unverifiable_execution_provenance,
    require_run_contract,
    verify_current_source,
)
from simpleclaw.llm.models import LLMResponse, LLMRoute
from simpleclaw.llm.router import LLMRouter


@pytest.fixture(autouse=True)
def _stub_verified_source_for_artifact_boundary_tests(monkeypatch) -> None:
    """CI shallow clone과 무관하게 artifact/report 경계만 격리 검증한다."""
    source_hashes = RUN_CONTRACT["task_owned_source"]["files"]
    provenance = {
        "status": "verified",
        "prerequisite_merge_sha": PREREQUISITE_MERGE_SHA,
        "task_owned_source_hashes": source_hashes,
        "task_owned_source_set_fingerprint": canonical_json_sha256(
            source_hashes
        ),
    }
    monkeypatch.setattr(
        functiongemma_poc,
        "verify_current_source",
        lambda *_args, **_kwargs: provenance,
    )


def test_default_budget_enforces_token_hard_cap() -> None:
    assert LabelingBudget().max_tokens == MAX_PROVIDER_TOKENS


def test_cli_default_propagates_token_hard_cap() -> None:
    args = _parser().parse_args([
        "label",
        "--private-output-dir",
        "/tmp/functiongemma-private",
    ])
    assert args.max_provider_tokens == LabelingBudget().max_tokens


def _write_run_manifest(output) -> None:
    invalidated = output.parent / f"{output.name}-invalidated"
    invalidated.mkdir()
    (invalidated / "INVALIDATED.json").write_text(
        '{"status":"invalidated"}',
        encoding="utf-8",
    )
    preflight_fresh_run(
        output,
        invalidated_artifact_dirs=[invalidated],
    )


def test_clean_rerun_requires_empty_output_and_invalidation_marker(
    tmp_path,
) -> None:
    invalidated = tmp_path / "old"
    invalidated.mkdir()
    (invalidated / "INVALIDATED.json").write_text(
        '{"status":"invalidated"}',
        encoding="utf-8",
    )
    output = tmp_path / "fresh"
    resolved = preflight_fresh_run(
        output,
        invalidated_artifact_dirs=[invalidated],
    )
    manifest = json.loads(
        (resolved / "run-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["run_contract_fingerprint"] == RUN_CONTRACT_FINGERPRINT
    assert RUN_CONTRACT["reviewed_prerequisite"]["merge_sha"] == (
        PREREQUISITE_MERGE_SHA
    )
    assert manifest["execution_source_provenance"]["status"] == "verified"
    assert manifest["execution_source_provenance"][
        "task_owned_source_hashes"
    ]
    assert manifest["training_process_invocation_count"] == 0
    assert len(manifest["invalidated_artifacts_excluded"]) == 1

    with pytest.raises(FileExistsError, match="fresh"):
        preflight_fresh_run(
            output,
            invalidated_artifact_dirs=[invalidated],
        )


def test_run_contract_rejects_missing_source_provenance(tmp_path) -> None:
    output = tmp_path / "private"
    _write_run_manifest(output)
    manifest_path = output / "run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("execution_source_provenance")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(TypeError, match="source provenance"):
        require_run_contract(output)


def test_source_lock_mismatch_fails_closed() -> None:
    relative_name = next(iter(RUN_CONTRACT["task_owned_source"]["files"]))
    with pytest.raises(RuntimeError, match="source hash mismatch"):
        verify_current_source(source_lock={
            "files": {relative_name: "0" * 64},
        })


@pytest.mark.parametrize(
    ("stop_reason", "returncode", "failure_key"),
    (
        ("disk_cap", -15, "training.disk_cap"),
        ("time_cap", -15, "training.time_cap"),
        ("completed", 1, "training.process_error"),
    ),
)
def test_training_failure_report_preserves_real_boundary_reason(
    tmp_path,
    stop_reason,
    returncode,
    failure_key,
) -> None:
    output = tmp_path / "private"
    _write_run_manifest(output)
    (output / "lineage-manifest.json").write_text(json.dumps({
        "provider_usage": {"provider_calls": 1},
        "provider_payload_audit": {
            "payload_count": 1,
            "accepted_target_out_of_pre_call_set_count": 0,
            "raw_identifier_match_count": 0,
            "payload_set_fingerprint": "a" * 64,
            "fingerprint_algorithm": "SHA-256",
            "canonicalization": "canonical",
        },
    }), encoding="utf-8")

    record_training_failure(output, manifest={
        "returncode": returncode,
        "stop_reason": stop_reason,
        "elapsed_seconds": 1.0,
        "artifact_bytes": 0,
        "peak_artifact_bytes": 0,
        "artifact_cap_bytes": 10,
        "process_invocation_count": 1,
        "adapter_path": str(output / "adapter"),
        "consumed_budget": {"steps_requested": 2},
    }, error_code="RuntimeError")

    report = json.loads(
        (output / "aggregate-report.json").read_text(encoding="utf-8")
    )
    assert report["hard_failures"] == {failure_key: 1}
    assert report["training_budget"]["stop_reason"] == failure_key.removeprefix(
        "training."
    )
    assert report["recommend_shadow_integration"] is False
    assert report["raw_text_rows"] == 0
    assert report["report_fingerprints"]["canonical_payload"]["value"] != (
        report["report_fingerprints"]["private_report_file_bytes"]["value"]
    )
    training_manifest = json.loads(
        (output / "training-manifest.json").read_text(encoding="utf-8")
    )
    assert training_manifest["stop_reason"] == failure_key.removeprefix(
        "training."
    )
    run_manifest = json.loads(
        (output / "run-manifest.json").read_text(encoding="utf-8")
    )
    assert run_manifest["training_stop_reason"] == failure_key.removeprefix(
        "training."
    )


def test_existing_uncommitted_run_records_lineage_hard_failure(
    tmp_path,
) -> None:
    output = tmp_path / "private"
    output.mkdir()
    (output / "run-manifest.json").write_text(
        json.dumps({"status": "training_failed"}),
        encoding="utf-8",
    )
    (output / "lineage-manifest.json").write_text("{}", encoding="utf-8")
    (output / "private-hard-failure-report.json").write_text(
        json.dumps({
            "status": "hard_failure",
            "hard_failures": {"training.process_error": 1},
            "recommend_shadow_integration": False,
        }),
        encoding="utf-8",
    )

    result = record_unverifiable_execution_provenance(
        output,
        checkout_base_commit_sha=PREREQUISITE_MERGE_SHA,
        first_persisted_post_run_commit_sha="8" * 40,
        evidence=["Multica run messages", "Git commit history"],
    )

    report = json.loads(
        (output / "aggregate-report.json").read_text(encoding="utf-8")
    )
    assert report["hard_failures"] == {
        "lineage.execution_source_unverifiable": 1,
        "training.process_error": 1,
    }
    assert report["execution_source_provenance"]["status"] == "unverifiable"
    assert result["private_hard_failure_report_file_sha256"] == (
        report["report_fingerprints"]["private_report_file_bytes"]["value"]
    )


def _case(number: int) -> SanitizedCase:
    return SanitizedCase(
        f"case:{number}", f"group:{number}", (), f"private-{number}",
        "telegram", f"fp:{number}", "train",
    )


def _asset(name: str = "search") -> PlannerAsset:
    return PlannerAsset(
        asset_type="skill",
        name=name,
        description="search",
        domains=("news",),
        intents=("lookup",),
        read_only=True,
        side_effects=False,
        freshness_sensitive=True,
        direct_answer=True,
        requires_confirmation=False,
        output_contract=None,
        declared=True,
        runtime_visible=True,
    )


def _plan(
    confidence: float = 0.9,
    asset_name: str = "search",
) -> UnifiedTurnPlan:
    return UnifiedTurnPlan(
        original_text="private",
        context=ContextSelection(ContextRelation.STANDALONE, False, (), "q"),
        clarification=ClarificationPlan(False),
        domains=("news",),
        intents=("lookup",),
        fact_check=FactCheckPlan(False, EvidenceOwner.NONE, "", (), ""),
        execution=ExecutionPlan(
            ExecutionMode.EXECUTE_ASSET,
            AssetRef("skill", asset_name),
            (AssetRef("skill", asset_name),),
            ("execute_skill",),
            False,
            "",
        ),
        confidence=confidence,
        decision_summary="",
    )


def _planner_response(
    *,
    primary_asset: str,
    allowed_assets: tuple[str, ...],
) -> str:
    def asset_ref(name: str) -> dict[str, str]:
        return {"asset_type": "skill", "asset_name": name}

    return json.dumps({
        "context": {
            "relation": "standalone",
            "use_prior_context": False,
            "selected_turn_ids": [],
            "standalone_question": "현재 질문",
            "unresolved_references": [],
            "ignored_context_reason": "",
        },
        "clarification": {
            "required": False,
            "question": "",
            "options": [],
            "reason": "",
        },
        "domains": ["news"],
        "intents": ["lookup"],
        "fact_check": {
            "required": False,
            "owner": "none",
            "domain": "",
            "entities": [],
            "search_query": "",
            "required_claims": [],
            "freshness_required": False,
            "reason": "",
        },
        "execution": {
            "mode": "execute_asset",
            "primary_asset": asset_ref(primary_asset),
            "allowed_assets": [asset_ref(name) for name in allowed_assets],
            "allowed_tools": ["execute_skill"],
            "requires_confirmation": False,
            "reason": "",
        },
        "confidence": 0.9,
        "decision_summary": "",
    })


@pytest.mark.asyncio
async def test_provider_requires_explicit_opt_in() -> None:
    async def planner(case, candidates):
        raise AssertionError("must not call")

    with pytest.raises(PermissionError):
        await label_cases(
            [_case(1)],
            catalog_assets=[_asset()],
            planner=planner,
            allow_provider_calls=False,
        )


@pytest.mark.asyncio
async def test_budget_stops_calls_and_low_confidence_goes_to_queue() -> None:
    calls = 0

    async def planner(case, candidates):
        nonlocal calls
        calls += 1
        return PlannerResponse(
            _plan(0.4 if case.case_id.endswith("1") else 0.9),
            token_count=1,
        )

    result = await label_cases(
        [_case(1), _case(2), _case(3)],
        catalog_assets=[_asset()],
        planner=planner,
        allow_provider_calls=True,
        budget=LabelingBudget(max_calls=2),
    )
    assert calls == result.provider_calls == 2
    assert len(result.labeled) == 1
    assert {reason for item in result.adjudication_queue for reason in item.reason_codes} == {
        "confidence.low",
        "budget.call_exhausted",
    }
    summary = labeling_public_summary(result)
    assert "private-1" not in str(summary)


@pytest.mark.asyncio
async def test_in_flight_timeout_cancels_and_rejects_label() -> None:
    cancelled = False

    async def planner(case, candidates):
        nonlocal cancelled
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            cancelled = True
            raise
        return PlannerResponse(_plan(), token_count=1)

    result = await label_cases(
        [_case(1)],
        catalog_assets=[_asset()],
        planner=planner,
        allow_provider_calls=True,
        budget=LabelingBudget(max_seconds=0.01),
    )
    assert cancelled
    assert result.labeled == ()
    assert result.adjudication_queue[0].reason_codes == (
        "budget.time_exhausted",
    )


@pytest.mark.asyncio
async def test_post_call_deadline_result_is_rejected() -> None:
    times = iter((0.0, 0.0, 0.0, 0.02, 0.02))

    async def planner(case, candidates):
        return PlannerResponse(_plan(), token_count=1)

    result = await label_cases(
        [_case(1)],
        catalog_assets=[_asset()],
        planner=planner,
        allow_provider_calls=True,
        budget=LabelingBudget(max_seconds=0.01),
        clock=lambda: next(times),
    )
    assert result.labeled == ()
    assert result.adjudication_queue[0].reason_codes == (
        "budget.time_exhausted",
    )


@pytest.mark.asyncio
async def test_token_cap_rejects_overshoot_and_stops_next_call() -> None:
    calls = 0

    async def planner(case, candidates):
        nonlocal calls
        calls += 1
        return PlannerResponse(_plan(), token_count=6)

    result = await label_cases(
        [_case(1), _case(2)],
        catalog_assets=[_asset()],
        planner=planner,
        allow_provider_calls=True,
        budget=LabelingBudget(max_tokens=5),
    )
    assert calls == 1
    assert result.labeled == ()
    assert result.provider_tokens == 6
    assert all(
        item.reason_codes == ("budget.token_exhausted",)
        for item in result.adjudication_queue
    )


@pytest.mark.asyncio
async def test_unknown_token_usage_fails_closed_when_cap_enabled() -> None:
    async def planner(case, candidates):
        return _plan()

    result = await label_cases(
        [_case(1)],
        catalog_assets=[_asset()],
        planner=planner,
        allow_provider_calls=True,
        budget=LabelingBudget(max_tokens=10),
    )
    assert result.labeled == ()
    assert not result.token_usage_supported
    assert result.adjudication_queue[0].reason_codes == (
        "budget.token_usage_unavailable",
    )


@pytest.mark.asyncio
async def test_out_of_set_target_is_not_promoted_after_planner_call() -> None:
    exposed: tuple[str, ...] = ()

    async def planner(case, candidates):
        nonlocal exposed
        exposed = tuple(candidate.asset_id for candidate in candidates)
        return PlannerResponse(_plan(asset_name="outside"), token_count=1)

    result = await label_cases(
        [_case(1)],
        catalog_assets=[_asset()],
        planner=planner,
        allow_provider_calls=True,
    )

    assert exposed == ("skill:search", "__none__")
    assert result.labeled == ()
    assert result.adjudication_queue[0].reason_codes == (
        "boundary.unknown_asset",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("primary_asset", "allowed_assets"),
    (
        ("outside", ("outside",)),
        ("search", ("search", "outside")),
    ),
    ids=("out-of-set-primary", "out-of-set-allowed"),
)
async def test_production_label_wrapper_preserves_unknown_asset_reason(
    tmp_path,
    monkeypatch,
    primary_asset: str,
    allowed_assets: tuple[str, ...],
) -> None:
    output = tmp_path / "private"
    output.mkdir()
    _write_run_manifest(output)
    (output / "sanitized-cases.jsonl").write_text(
        json.dumps(_case(1).to_dict()) + "\n",
        encoding="utf-8",
    )
    (output / "lineage-manifest.json").write_text("{}", encoding="utf-8")
    catalog = PlannerCatalog(assets=(_asset(),), fingerprint="full")
    response = LLMResponse(text=_planner_response(
        primary_asset=primary_asset,
        allowed_assets=allowed_assets,
    ))
    primary = AsyncMock()
    primary.send = AsyncMock(return_value=response)
    retry = AsyncMock()
    retry.send = AsyncMock(return_value=response)
    router = LLMRouter(
        backends={},
        providers={"primary": primary, "retry": retry},
        default_backend="primary",
        routes={
            "turn_analysis": LLMRoute(
                "turn_analysis",
                "primary",
                "retry",
            )
        },
    )
    monkeypatch.setattr(intent_poc, "create_router", lambda _config: router)
    monkeypatch.setattr(intent_poc, "_runtime_catalog", lambda _config: catalog)

    await intent_poc._label_async(
        Namespace(
            config="unused.yaml",
            allow_provider_calls=True,
            max_provider_calls=1,
            max_provider_seconds=3600,
            max_provider_tokens=MAX_PROVIDER_TOKENS,
        ),
        output,
    )
    lineage = json.loads(
        (output / "lineage-manifest.json").read_text(encoding="utf-8")
    )
    audit = lineage["provider_payload_audit"]
    assert audit["payload_count"] > 0
    assert audit["raw_identifier_match_count"] == 0
    assert all(len(value) == 64 for value in audit["payload_fingerprints"])

    summary = json.loads(
        (output / "labeling-summary.json").read_text(encoding="utf-8")
    )
    queue = [
        json.loads(line)
        for line in (output / "adjudication-queue.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert summary["labeled_count"] == 0
    assert summary["adjudication_reasons"] == {"boundary.unknown_asset": 1}
    assert queue[0]["reason_codes"] == ["boundary.unknown_asset"]
    primary.send.assert_awaited_once()
    retry.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_pre_call_candidates_and_fingerprint_remain_identical() -> None:
    captured: tuple[str, ...] = ()

    async def planner(case, candidates):
        nonlocal captured
        captured = tuple(candidate.asset_id for candidate in candidates)
        return PlannerResponse(_plan(), token_count=1)

    result = await label_cases(
        [_case(1)],
        catalog_assets=[_asset()],
        planner=planner,
        allow_provider_calls=True,
    )

    assert len(result.labeled) == 1
    labeled = result.labeled[0]
    assert tuple(candidate.asset_id for candidate in labeled.candidates) == captured
    assert labeled.candidate_fingerprint == candidate_fingerprint(
        labeled.candidates
    )


def test_bounded_catalog_and_actual_prompt_match_case_candidates() -> None:
    assets = (_asset("search"), _asset("weather"))
    catalog = PlannerCatalog(assets=assets, fingerprint="full")
    candidates = (
        CandidateAsset("skill:weather", "skill", "weather"),
        CandidateAsset("skill:search", "skill", "search"),
        CandidateAsset(NO_ASSET, "none", NO_ASSET),
    )
    case = SanitizedCase(
        "opaque-case-a",
        "source:fp",
        (
            SanitizedMessage(
                "opaque-turn-a",
                "user",
                "앞 질문",
            ),
        ),
        "현재 질문",
        "telegram",
        "fp",
    )

    bounded = _bounded_catalog(catalog, candidates)
    prompt = _provider_prompt_diagnostic(case, candidates, catalog)
    payload = json.loads(prompt)

    assert bounded.fingerprint == candidate_fingerprint(candidates)
    assert [
        f"{item['type']}:{item['name']}"
        for item in payload["capability_catalog"]
    ] == ["skill:weather", "skill:search"]
    assert payload["catalog_fingerprint"] == candidate_fingerprint(candidates)
    assert "101" not in prompt
    assert "102" not in prompt


def test_provider_prompt_diagnostic_rejects_raw_message_id() -> None:
    asset = _asset()
    catalog = PlannerCatalog(assets=(asset,), fingerprint="full")
    candidates = (
        CandidateAsset("skill:search", "skill", "search"),
        CandidateAsset(NO_ASSET, "none", NO_ASSET),
    )
    case = SanitizedCase(
        "opaque-case-a",
        "source:fp",
        (SanitizedMessage("msg:101", "user", "앞 질문"),),
        "현재 질문",
        "telegram",
        "fp",
    )

    with pytest.raises(ValueError, match="identifier_leak"):
        _provider_prompt_diagnostic(case, candidates, catalog)
