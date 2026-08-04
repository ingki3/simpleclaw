"""도메인 지식 없이 Recipe와 Skill 실행을 연결하는 adapter 경계."""

from .base import AdapterResponse, BoundSkillPayload, GenericAssetAdapter
from .cron import (
    CronGraphFacade,
    CronGraphResultV1,
    CronIngressAdapter,
    CronIngressV1,
)
from .recipe import GenericRecipeAdapter
from .skill import GenericSkillAdapter

__all__ = [
    "AdapterResponse",
    "BoundSkillPayload",
    "CronGraphFacade",
    "CronGraphResultV1",
    "CronIngressAdapter",
    "CronIngressV1",
    "GenericAssetAdapter",
    "GenericRecipeAdapter",
    "GenericSkillAdapter",
]
