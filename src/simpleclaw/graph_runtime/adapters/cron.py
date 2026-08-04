"""기존 Cron job을 V4 ingress와 scheduler 결과 계약으로 변환한다.

Scheduler는 schedule/circuit-break/실행 기록을 계속 소유하고, graph는 한 번의
Cron run 안에서 solver/tool retry와 delivery 상태를 소유한다. 이 adapter는 두
경계 사이의 값만 변환하며 notifier나 executor를 직접 호출하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, model_validator

from ..contracts import CronSourceV1, RequestEnvelopeV1
from ..status import DeliveryStatus, EffectStatus, TerminalOutcome

if TYPE_CHECKING:
    from simpleclaw.daemon.models import CronActionResult, CronJob


class CronAdapterModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CronIngressV1(CronAdapterModel):
    """Graph facade에 전달할 envelope와 optional Recipe 선택 정보다."""

    envelope: RequestEnvelopeV1
    checkpoint_thread_id: str
    preselected_recipe_ref: str | None = None

    @model_validator(mode="after")
    def require_exact_checkpoint_identity(self) -> CronIngressV1:
        if self.checkpoint_thread_id != self.envelope.request_id:
            raise ValueError("cron checkpoint identity must equal request identity")
        return self


class CronGraphResultV1(CronAdapterModel):
    """Graph facade가 scheduler에 반환하는 domain-neutral 완료 축이다."""

    content: str = ""
    terminal_outcome: TerminalOutcome
    delivery_status: DeliveryStatus = DeliveryStatus.READY
    effect_status: EffectStatus = EffectStatus.NONE


class CronGraphFacade(Protocol):
    """CronScheduler가 opt-in으로 주입받는 최소 V4 runtime 표면이다."""

    async def execute_cron(self, ingress: CronIngressV1) -> CronGraphResultV1: ...


@dataclass(frozen=True)
class CronIngressAdapter:
    """Cron job identity를 immutable graph ingress로 정규화한다."""

    locale: str = "ko-KR"

    def normalize(
        self,
        job: CronJob,
        *,
        run_id: str | None = None,
        received_at: datetime | None = None,
        deadline_at: datetime | None = None,
    ) -> CronIngressV1:
        normalized_run_id = run_id or uuid4().hex
        timestamp = received_at or datetime.now(UTC)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        recipe_ref = (
            job.action_reference
            if getattr(job.action_type, "value", None) == "recipe"
            else None
        )
        envelope = RequestEnvelopeV1(
            request_id=f"cron:{normalized_run_id}",
            source="cron",
            session_key=f"cron:{job.name}",
            received_at=timestamp,
            original_text=job.action_reference,
            cron=CronSourceV1(job_id=job.name, run_id=normalized_run_id),
            deadline_at=deadline_at,
            locale=self.locale,
        )
        return CronIngressV1(
            envelope=envelope,
            checkpoint_thread_id=envelope.request_id,
            preselected_recipe_ref=recipe_ref,
        )

    def map_result(self, result: CronGraphResultV1) -> CronActionResult:
        """Graph retry/delivery 소유권을 보존한 scheduler 결과로 변환한다."""
        # daemon package import는 scheduler module 초기화가 끝난 실행 시점까지
        # 늦춰 graph_runtime.contracts의 독립 import 경계를 보존한다.
        from simpleclaw.daemon.models import CronActionResult

        resolved = result.terminal_outcome in {
            TerminalOutcome.COMPLETED,
            TerminalOutcome.PARTIAL,
        }
        scheduler_may_notify = result.delivery_status in {
            DeliveryStatus.NOT_READY,
            DeliveryStatus.READY,
        }
        # empty output, shadow/no-send, 이미 graph가 보낸 결과, 불명 delivery는
        # 모두 scheduler notifier에서 fail-closed로 억제한다.
        notify = resolved and scheduler_may_notify and bool(result.content.strip())
        return CronActionResult(
            text=result.content,
            success=resolved,
            retryable=False,
            notify=notify,
        )
