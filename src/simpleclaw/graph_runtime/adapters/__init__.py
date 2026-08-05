"""도메인 지식 없이 Recipe와 Skill 실행을 연결하는 adapter 경계."""

from .base import AdapterResponse, BoundSkillPayload, GenericAssetAdapter
from .cron import (
    CronGraphFacade,
    CronGraphResultV1,
    CronIngressAdapter,
    CronIngressV1,
)
from .delivery import (
    AdapterDeliveryResult,
    CallbackDeliveryAdapter,
    CronDeliveryAdapter,
    DeliveryAdapter,
    NullDeliveryAdapter,
    SenderReceipt,
    SendNotStartedError,
    TelegramDeliveryAdapter,
)
from .native_tool import GenericNativeToolAdapter
from .persistence import ConversationStorePersistenceAdapter
from .recipe import GenericRecipeAdapter
from .skill import GenericSkillAdapter

__all__ = [
    "AdapterDeliveryResult",
    "AdapterResponse",
    "BoundSkillPayload",
    "CallbackDeliveryAdapter",
    "ConversationStorePersistenceAdapter",
    "CronDeliveryAdapter",
    "CronGraphFacade",
    "CronGraphResultV1",
    "CronIngressAdapter",
    "CronIngressV1",
    "DeliveryAdapter",
    "GenericAssetAdapter",
    "GenericNativeToolAdapter",
    "GenericRecipeAdapter",
    "GenericSkillAdapter",
    "NullDeliveryAdapter",
    "SendNotStartedError",
    "SenderReceipt",
    "TelegramDeliveryAdapter",
]
