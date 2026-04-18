"""Data models for communication channels."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ChannelError(Exception):
    """Base error for channel operations."""


class TelegramError(ChannelError):
    """Error in Telegram bot operations."""


class WebhookError(ChannelError):
    """Error in webhook operations."""


class EventActionType(Enum):
    """Type of action a webhook event triggers."""

    PROMPT = "prompt"
    RECIPE = "recipe"


@dataclass
class AccessAttempt:
    """A log entry for access control."""

    source: str  # "telegram" or "webhook"
    user_identifier: str
    timestamp: datetime = field(default_factory=datetime.now)
    authorized: bool = False
    details: str = ""


@dataclass
class WebhookEvent:
    """An incoming webhook event payload."""

    event_type: str
    action_type: EventActionType | None = None
    action_reference: str = ""
    payload: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
