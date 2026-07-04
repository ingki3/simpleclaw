"""Typed data structures for complex factual/scenario workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class SlotStatus(str, Enum):
    MISSING = "missing"
    FILLED = "filled"
    PARTIAL = "partial"
    STALE = "stale"
    CONFLICTING = "conflicting"
    UNSUPPORTED = "unsupported"


class EvidenceCoverage(str, Enum):
    UNKNOWN = "unknown"
    PRE_EVENT = "pre_event"
    STALE = "stale"
    PARTIAL = "partial"
    CURRENT_PENDING = "current_pending"
    FINAL = "final"


@dataclass
class EvidenceItem:
    source_url: str
    source_title: str = ""
    source_type: Literal["official", "major_news", "specialist", "blog", "unknown"] = "unknown"
    claim: str = ""
    extracted_value: str | None = None
    source_time: str | None = None
    coverage: EvidenceCoverage = EvidenceCoverage.UNKNOWN
    confidence: Literal["low", "medium", "high"] = "medium"
    raw_excerpt: str = ""


@dataclass
class EvidenceSlot:
    name: str
    question: str
    required: bool = True
    freshness_required: bool = True
    preferred_source_type: str | None = None
    depends_on: list[str] = field(default_factory=list)
    status: SlotStatus = SlotStatus.MISSING
    evidence: list[EvidenceItem] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def add_evidence(self, item: EvidenceItem) -> None:
        self.evidence.append(item)
        if item.coverage == EvidenceCoverage.FINAL and item.confidence in {"medium", "high"}:
            self.status = SlotStatus.FILLED
        elif item.coverage == EvidenceCoverage.CURRENT_PENDING and item.confidence in {"medium", "high"}:
            self.status = SlotStatus.FILLED
            if "current_pending" not in self.limitations:
                self.limitations.append("current_pending")
        elif self.status == SlotStatus.MISSING:
            self.status = SlotStatus.PARTIAL


@dataclass
class FactPlan:
    task_type: str
    complexity_score: int
    slots: list[EvidenceSlot]
    requires_calculation: bool = False
    max_iterations: int = 4
    answer_contract: str = ""

    def missing_required_slots(self) -> list[EvidenceSlot]:
        return [
            slot for slot in self.slots
            if slot.required and slot.status != SlotStatus.FILLED
        ]

    def required_slots_complete(self) -> bool:
        return not self.missing_required_slots()


@dataclass
class ComplexFactResult:
    text: str
    plan: FactPlan
    success: bool
    limitations: list[str] = field(default_factory=list)
