"""Semantic memory and dreaming pipeline."""

from simpleclaw.memory.models import (
    ClusterRecord,
    ConversationMessage,
    DreamingError,
    MemoryEntry,
    MemoryError,
    MessageRole,
)
from simpleclaw.memory.clustering import ClusterAssignment, IncrementalClusterer
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.dreaming import DreamingPipeline
from simpleclaw.memory.embedding_service import EmbeddingService
from simpleclaw.memory.protected_section import (
    ManagedSection,
    ProtectedSectionError,
    ProtectedSectionMalformed,
    ProtectedSectionMissing,
    append_to_section,
    build_initial_template,
    ensure_initialized,
    find_managed_sections,
    get_managed_section,
    get_section_body,
    has_managed_section,
    replace_section_body,
)

__all__ = [
    "ClusterAssignment",
    "ClusterRecord",
    "ConversationMessage",
    "ConversationStore",
    "DreamingError",
    "DreamingPipeline",
    "EmbeddingService",
    "IncrementalClusterer",
    "ManagedSection",
    "MemoryEntry",
    "MemoryError",
    "MessageRole",
    "ProtectedSectionError",
    "ProtectedSectionMalformed",
    "ProtectedSectionMissing",
    "append_to_section",
    "build_initial_template",
    "ensure_initialized",
    "find_managed_sections",
    "get_managed_section",
    "get_section_body",
    "has_managed_section",
    "replace_section_body",
]
