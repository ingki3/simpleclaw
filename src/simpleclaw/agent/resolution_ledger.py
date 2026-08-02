"""Capability-first 실행의 evidence/action/attempt 공통 ledger."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from simpleclaw.agent.resolution_types import AssetResult


@dataclass(frozen=True)
class EvidenceRecord:
    """Validator가 소비할 provenance/freshness 포함 evidence record."""

    claim_id: str
    value: Any = None
    source_url: str = ""
    observed_at: str = ""
    provenance: str = ""
    fresh: bool | None = None
    usable: bool = True
    limitation: str = ""


@dataclass(frozen=True)
class ActionRecord:
    """Side-effect 실행 상태와 effect identity."""

    asset_name: str
    status: str
    effect_id: str = ""
    side_effect: bool = False
    detail: str = ""


def attempt_signature(
    *,
    question: str,
    asset_type: str,
    asset_name: str,
    parameters: dict[str, Any] | None = None,
) -> str:
    """원문을 노출하지 않는 deterministic SHA-256 attempt signature."""
    payload = {
        "question": " ".join(question.split()),
        "asset_type": asset_type,
        "asset_name": asset_name,
        "parameters": parameters or {},
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class ResolutionLedger:
    """한 turn 동안 controller 사이에서 승계되는 단일 실행 ledger."""

    asset_results: list[AssetResult] = field(default_factory=list)
    evidence: list[EvidenceRecord] = field(default_factory=list)
    actions: list[ActionRecord] = field(default_factory=list)
    attempted_signatures: set[str] = field(default_factory=set)
    steps_used: int = 0
    tool_calls_used: int = 0
    tokens_used: int = 0

    def has_attempted(self, signature: str) -> bool:
        return signature in self.attempted_signatures

    def record_attempt(self, signature: str) -> bool:
        """처음 보는 signature만 기록하고 True를 반환한다."""
        if signature in self.attempted_signatures:
            return False
        self.attempted_signatures.add(signature)
        return True

    def append_asset_result(self, result: AssetResult) -> None:
        """Asset result와 동봉된 evidence/action 상태를 함께 기록한다."""
        self.asset_results.append(result)
        for item in result.evidence:
            self.evidence.append(
                EvidenceRecord(
                    claim_id=str(item.get("claim_id") or item.get("claim") or ""),
                    value=item.get("value", item.get("data")),
                    source_url=str(item.get("source_url") or item.get("url") or ""),
                    observed_at=str(item.get("observed_at") or item.get("as_of") or ""),
                    provenance=str(item.get("provenance") or item.get("source") or ""),
                    fresh=item.get("fresh") if isinstance(item.get("fresh"), bool) else None,
                    usable=bool(item.get("usable", True)),
                    limitation=str(item.get("limitation") or ""),
                )
            )
        if result.side_effect:
            self.actions.append(
                ActionRecord(
                    asset_name=result.asset_name,
                    status=result.status.value,
                    effect_id=result.effect_id,
                    side_effect=True,
                    detail="; ".join(result.limitations),
                )
            )

    def record_usage(
        self,
        *,
        steps: int = 0,
        tool_calls: int = 0,
        tokens: int = 0,
    ) -> None:
        """Controller 경계를 넘어 승계할 누적 실행 사용량을 기록한다."""
        self.steps_used += max(0, steps)
        self.tool_calls_used += max(0, tool_calls)
        self.tokens_used += max(0, tokens)
