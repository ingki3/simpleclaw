"""Domain-neutral Recipe/Skill adapter boundary."""

from .base import AdapterResponse, BoundSkillPayload, GenericAssetAdapter
from .recipe import GenericRecipeAdapter
from .skill import GenericSkillAdapter

__all__ = [
    "AdapterResponse",
    "BoundSkillPayload",
    "GenericAssetAdapter",
    "GenericRecipeAdapter",
    "GenericSkillAdapter",
]
