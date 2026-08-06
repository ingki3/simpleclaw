"""BIZ-606 hermetic validator exact contract-set 회귀."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import pytest

import simpleclaw.langgraph_v4_shadow_validation as validation
from simpleclaw.graph_runtime.contracts_registry import build_contract_registry
from simpleclaw.langgraph_v4_shadow_validation import (
    EXPECTED_CONTRACT_SET,
    ContractIdentity,
    _assert_contract_set,
    _contract_identity,
    _contract_set_violations,
    _definitions,
    _run,
)

ROOT = Path(__file__).parents[2]


def _fixture_contract_set() -> frozenset[ContractIdentity]:
    registry = build_contract_registry(_definitions())
    return frozenset(
        _contract_identity(descriptor.ref)
        for entry in registry.entries
        for descriptor in (entry.input_descriptor, entry.output_descriptor)
    )


def test_fixture_contracts_match_canonical_exact_set() -> None:
    assert _fixture_contract_set() == EXPECTED_CONTRACT_SET
    assert _contract_set_violations(_fixture_contract_set()) == ()


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

    violations = _contract_set_violations(actual)

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
    missing = _contract_set_violations(frozenset(EXPECTED_CONTRACT_SET - {wanted}))
    extra_identity = ContractIdentity("skill", "extra", "extra.output", "1", "hash")
    extra = _contract_set_violations(
        frozenset((*EXPECTED_CONTRACT_SET, extra_identity))
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

    violations = _contract_set_violations(actual)

    assert len(violations) == 1
    assert violations[0].kind == "drift"
    assert violations[0].expected == wanted
    assert violations[0].actual == drifted
    assert violations[0].fields == expected_fields
    with pytest.raises(RuntimeError, match="exact-set mismatch"):
        _assert_contract_set(violations)


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

    assert await _run(args) == 0
    output = capsys.readouterr().out
    assert "HERMETIC_PLANNER=PASS" in output
    assert "EXTERNAL_PROVIDER_CALLS=0" in output
    assert "CONTRACT_SET_VIOLATIONS=[]" in output
    assert "TELEGRAM_SEND_COUNT=0" in output
    assert "CRON_NOTIFIER_COUNT=0" in output
    assert "CONVERSATION_WRITE_COUNT=0" in output


def test_offline_workflow_runs_hermetic_validator_with_all_assertions() -> None:
    workflow = (ROOT / ".github/workflows/offline-integration.yml").read_text()

    assert "python scripts/dev/validate_langgraph_v4_no_send.py" in workflow
    assert "--hermetic" in workflow
    assert "--assert-contract-set" in workflow
    assert "--assert-zero-delivery" in workflow
    assert "--assert-zero-persistence" in workflow
