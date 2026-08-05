"""Shadow run 동안 production 외부 callback 진입을 task-local로 계측한다."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

SideEffectKind = Literal["telegram_send", "conversation_write", "notifier"]


@dataclass(slots=True)
class ShadowSideEffectMonitor:
    """실제 adapter call point가 호출한 횟수를 한 shadow task에만 누적한다."""

    telegram_send: int = 0
    conversation_write: int = 0
    notifier: int = 0

    def record(self, kind: SideEffectKind) -> None:
        """알 수 없는 종류를 허용하지 않고 production callback 진입을 기록한다."""
        setattr(self, kind, getattr(self, kind) + 1)


_ACTIVE_MONITOR: ContextVar[ShadowSideEffectMonitor | None] = ContextVar(
    "simpleclaw_shadow_side_effect_monitor",
    default=None,
)


def record_shadow_side_effect(kind: SideEffectKind) -> None:
    """Shadow capture가 활성인 경우에만 현재 task의 callback 진입을 기록한다."""
    monitor = _ACTIVE_MONITOR.get()
    if monitor is not None:
        monitor.record(kind)


@contextmanager
def capture_shadow_side_effects() -> Iterator[ShadowSideEffectMonitor]:
    """setup 이후 한 run의 production callback delta만 격리해 수집한다."""
    monitor = ShadowSideEffectMonitor()
    token = _ACTIVE_MONITOR.set(monitor)
    try:
        yield monitor
    finally:
        _ACTIVE_MONITOR.reset(token)
