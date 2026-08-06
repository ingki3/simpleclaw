"""BIZ-606 hermetic validator exact contract-set 회귀."""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

import simpleclaw.langgraph_v4_shadow_validation as validation
from scripts.dev import validate_langgraph_v4_no_send as fixture_validation
from scripts.dev.validate_langgraph_v4_no_send import (
    EXPECTED_CONTRACT_SET,
    definitions as _definitions,
)
from simpleclaw.graph_runtime.contracts_registry import build_contract_registry
from simpleclaw.langgraph_v4_shadow_validation import (
    ContractIdentity,
    _assert_contract_set,
    _contract_identity,
    _contract_set_violations,
    _parser,
)

ROOT = Path(__file__).parents[2]


_VALIDATOR_SUBPROCESS = r"""
import sys
from dataclasses import replace

sys.path.insert(0, "src")
import simpleclaw.langgraph_v4_shadow_validation as validation
from scripts.dev import validate_langgraph_v4_no_send as scenario

mode, violation = sys.argv[1:]
validation.create_router = lambda _config: scenario.HermeticPlannerRouter()

if violation == "contract":
    wanted = min(scenario.EXPECTED_CONTRACT_SET)
    validation._contract_set_violations = lambda *_args, **_kwargs: (
        validation.ContractSetViolation(kind="missing", expected=wanted),
    )
elif violation in {"delivery", "persistence"}:
    original_run = validation.ConnectedShadowTurnRunner.run

    async def run_with_violation(self, *args, **kwargs):
        result = await original_run(self, *args, **kwargs)
        field = "telegram_send" if violation == "delivery" else "conversation_write"
        counts = replace(result.side_effect_counts, **{field: 1})
        return replace(result, side_effect_counts=counts)

    validation.ConnectedShadowTurnRunner.run = run_with_violation

cli_args = ["validator"]
if mode == "hermetic":
    cli_args.append("--hermetic")
else:
    cli_args.extend(("--config", "pyproject.toml"))
sys.argv = cli_args
raise SystemExit(__import__("asyncio").run(scenario.run(validation._parser().parse_args())))
"""


def _fixture_contract_set() -> frozenset[ContractIdentity]:
    registry = build_contract_registry(_definitions())
    return frozenset(
        _contract_identity(descriptor.ref)
        for entry in registry.entries
        for descriptor in (entry.input_descriptor, entry.output_descriptor)
    )


def test_fixture_contracts_match_canonical_exact_set() -> None:
    assert _fixture_contract_set() == EXPECTED_CONTRACT_SET
    assert _contract_set_violations(
        _fixture_contract_set(), expected=EXPECTED_CONTRACT_SET
    ) == ()


def test_same_count_wrong_member_reports_missing_and_extra() -> None:
    wanted = min(EXPECTED_CONTRACT_SET)
    wrong = ContractIdentity(
        owner_type="skill",
        owner_name="unrelated-fixture",
        contract_id="unrelated.contract",
        version="1",
        schema_hash="wrong-schema",
    )
    actual = frozenset((EXPECTED_CONTRACT_SET - {wanted}) | {wrong})

    violations = _contract_set_violations(actual, expected=EXPECTED_CONTRACT_SET)

    assert {item.kind for item in violations} == {"missing", "extra"}
    assert {item.expected for item in violations if item.kind == "missing"} == {
        wanted
    }
    assert {item.actual for item in violations if item.kind == "extra"} == {wrong}

    with pytest.raises(RuntimeError, match="exact-set mismatch") as error:
        _assert_contract_set(violations)
    assert '"kind":"missing"' in str(error.value)
    assert '"kind":"extra"' in str(error.value)


def test_missing_and_extra_members_are_reported_independently() -> None:
    wanted = min(EXPECTED_CONTRACT_SET)
    missing = _contract_set_violations(
        frozenset(EXPECTED_CONTRACT_SET - {wanted}),
        expected=EXPECTED_CONTRACT_SET,
    )
    extra_identity = ContractIdentity("skill", "extra", "extra.output", "1", "hash")
    extra = _contract_set_violations(
        frozenset((*EXPECTED_CONTRACT_SET, extra_identity)),
        expected=EXPECTED_CONTRACT_SET,
    )

    assert [(item.kind, item.expected) for item in missing] == [("missing", wanted)]
    assert [(item.kind, item.actual) for item in extra] == [("extra", extra_identity)]
    with pytest.raises(RuntimeError, match="exact-set mismatch"):
        _assert_contract_set(missing)
    with pytest.raises(RuntimeError, match="exact-set mismatch"):
        _assert_contract_set(extra)


@pytest.mark.parametrize(
    ("changes", "expected_fields"),
    [
        ({"owner_type": "recipe"}, ("owner_type",)),
        ({"owner_name": "renamed-owner"}, ("owner_name",)),
        ({"contract_id": "skill.changed.input"}, ("contract_id",)),
        ({"version": "2"}, ("version",)),
        ({"schema_hash": "changed-schema"}, ("schema_hash",)),
    ],
)
def test_owner_contract_version_and_schema_drift_are_structured(
    changes: dict[str, str],
    expected_fields: tuple[str, ...],
) -> None:
    wanted = next(
        item
        for item in EXPECTED_CONTRACT_SET
        if item.contract_id == "skill.contract-fixture-step.input"
    )
    drifted = replace(wanted, **changes)
    actual = frozenset((EXPECTED_CONTRACT_SET - {wanted}) | {drifted})

    violations = _contract_set_violations(actual, expected=EXPECTED_CONTRACT_SET)

    assert len(violations) == 1
    assert violations[0].kind == "drift"
    assert violations[0].expected == wanted
    assert violations[0].actual == drifted
    assert violations[0].fields == expected_fields
    with pytest.raises(RuntimeError, match="exact-set mismatch"):
        _assert_contract_set(violations)


@pytest.mark.parametrize("argv", [[], ["--hermetic"]])
def test_parser_enables_all_safety_assertions_by_default(argv: list[str]) -> None:
    args = _parser().parse_args(argv)

    assert args.assert_contract_set is True
    assert args.assert_zero_delivery is True
    assert args.assert_zero_persistence is True


@pytest.mark.parametrize("mode", ["default", "hermetic"])
@pytest.mark.parametrize("violation", ["contract", "delivery", "persistence"])
def test_cli_exits_nonzero_for_measured_safety_violation(
    mode: str,
    violation: str,
) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", _VALIDATOR_SUBPROCESS, mode, violation],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert f"{violation} " in completed.stderr.lower()


@pytest.mark.parametrize("mode", ["default", "hermetic"])
def test_cli_safe_fixture_exits_zero(mode: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", _VALIDATOR_SUBPROCESS, mode, "safe"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "CONTRACT_SET_VIOLATIONS=[]" in completed.stdout
    assert "TELEGRAM_SEND_COUNT=0" in completed.stdout
    assert "CRON_NOTIFIER_COUNT=0" in completed.stdout
    assert "CONVERSATION_WRITE_COUNT=0" in completed.stdout


@pytest.mark.asyncio
async def test_hermetic_validator_avoids_provider_and_conversation_store(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("external boundary must not be constructed")

    monkeypatch.setattr(validation, "create_router", forbidden)
    monkeypatch.setattr(validation, "ConversationStore", forbidden)
    args = argparse.Namespace(
        architecture="langgraph_v4",
        mode="shadow",
        repeat=1,
        max_provider_calls=12,
        deadline_seconds=300.0,
        backend="",
        config=ROOT / "does-not-exist.yaml",
        hermetic=True,
        assert_contract_set=True,
        assert_zero_delivery=True,
        assert_zero_persistence=True,
    )

    assert await fixture_validation.run(args) == 0
    output = capsys.readouterr().out
    assert "HERMETIC_PLANNER=PASS" in output
    assert "EXTERNAL_PROVIDER_CALLS=0" in output
    assert "CONTRACT_SET_VIOLATIONS=[]" in output
    assert "TELEGRAM_SEND_COUNT=0" in output
    assert "CRON_NOTIFIER_COUNT=0" in output
    assert "CONVERSATION_WRITE_COUNT=0" in output


def test_kbo_scenario_repeats_asset_zero_effective_plan_no_send() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/dev/validate_langgraph_v4_shadow.py",
            "--hermetic",
            "--mode",
            "primary",
            "--repeat",
            "3",
            "--max-provider-calls",
            "3",
            "--deadline-seconds",
            "30",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    output = completed.stdout
    assert output.count('"original_asset":null') == 3
    assert output.count('"effective_asset":"recipe:sports-live"') == 3
    assert "ASSET_DEFINITIONS=scenario_installer_output" in output
    assert "TARGET_DISPATCH_EXACTLY_ONCE=true" in output
    assert "TYPED_FINAL=PASS" in output
    assert "TELEGRAM_SEND_COUNT=0" in output
    assert "CRON_NOTIFIER_COUNT=0" in output
    assert "CONVERSATION_WRITE_COUNT=0" in output


def test_offline_workflow_runs_hermetic_validator_with_all_assertions() -> None:
    workflow = (ROOT / ".github/workflows/offline-integration.yml").read_text()

    assert "python scripts/dev/validate_langgraph_v4_no_send.py" in workflow
    assert "--hermetic" in workflow
    assert "--unsafe" not in workflow
    assert "--assert-contract-set" not in workflow
    assert "--assert-zero-delivery" not in workflow
    assert "--assert-zero-persistence" not in workflow
