"""기존 channel sender를 V4 delivery receipt로 변환하는 주입 경계다."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from ..contracts import DeliveryIntentV1
from ..status import DeliveryStatus

SenderCallback = Callable[
    [str, str], "SenderReceipt | None | Awaitable[SenderReceipt | None]"
]


class SendNotStartedError(RuntimeError):
    """channel adapter가 외부 전송 시작 전 실패를 증명할 때만 사용한다."""


@dataclass(frozen=True, slots=True)
class SenderReceipt:
    external_message_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class AdapterDeliveryResult:
    status: DeliveryStatus
    external_message_id: str | None = None
    detail: str | None = None


class DeliveryAdapter(Protocol):
    async def send(
        self, intent: DeliveryIntentV1, content: str
    ) -> AdapterDeliveryResult: ...


class CallbackDeliveryAdapter:
    """callback 진입 뒤 일반 예외를 UNKNOWN으로 보수 정규화한다."""

    def __init__(self, sender: SenderCallback) -> None:
        self._sender = sender

    async def send(
        self, intent: DeliveryIntentV1, content: str
    ) -> AdapterDeliveryResult:
        try:
            receipt = self._sender(intent.destination_ref, content)
            if inspect.isawaitable(receipt):
                receipt = await receipt
        except SendNotStartedError as exc:
            return AdapterDeliveryResult(
                DeliveryStatus.FAILED_BEFORE_SEND, detail=str(exc)
            )
        except Exception as exc:  # noqa: BLE001 - 외부 sender 오류를 UNKNOWN으로 정규화
            return AdapterDeliveryResult(DeliveryStatus.UNKNOWN, detail=str(exc))
        if receipt is None:
            receipt = SenderReceipt()
        if not isinstance(receipt, SenderReceipt):
            return AdapterDeliveryResult(
                DeliveryStatus.UNKNOWN, detail="sender returned an invalid receipt"
            )
        return AdapterDeliveryResult(
            DeliveryStatus.DELIVERED,
            external_message_id=receipt.external_message_id,
            detail=receipt.detail,
        )


class TelegramDeliveryAdapter(CallbackDeliveryAdapter):
    pass


class CronDeliveryAdapter(CallbackDeliveryAdapter):
    async def send(
        self, intent: DeliveryIntentV1, content: str
    ) -> AdapterDeliveryResult:
        if not content.strip() or content.lstrip().startswith("[NO_NOTIFY]"):
            return AdapterDeliveryResult(DeliveryStatus.SUPPRESSED)
        return await super().send(intent, content)


class NullDeliveryAdapter:
    """shadow 실행에서 live sender/notifier를 보유하지 않는 adapter다."""

    async def send(
        self, intent: DeliveryIntentV1, content: str
    ) -> AdapterDeliveryResult:
        return AdapterDeliveryResult(DeliveryStatus.SHADOWED)
