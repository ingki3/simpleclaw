"""도메인 지식 없이 Recipe와 Skill 실행을 연결하는 adapter 경계."""

from .base import AdapterResponse, BoundSkillPayload, GenericAssetAdapter
from .delivery import (
    AdapterDeliveryResult,
    CallbackDeliveryAdapter,
    CronDeliveryAdapter,
    DeliveryAdapter,
    NullDeliveryAdapter,
    SendNotStartedError,
    SenderReceipt,
    TelegramDeliveryAdapter,
)
from .recipe import GenericRecipeAdapter
from .persistence import ConversationStorePersistenceAdapter
from .skill import GenericSkillAdapter

__all__ = [
    "AdapterResponse",
    "AdapterDeliveryResult",
    "BoundSkillPayload",
    "CallbackDeliveryAdapter",
    "CronDeliveryAdapter",
    "ConversationStorePersistenceAdapter",
    "DeliveryAdapter",
    "GenericAssetAdapter",
    "GenericRecipeAdapter",
    "GenericSkillAdapter",
    "NullDeliveryAdapter",
    "SendNotStartedError",
    "SenderReceipt",
    "TelegramDeliveryAdapter",
]
