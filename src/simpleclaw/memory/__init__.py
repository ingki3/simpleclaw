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

__all__ = [
    "ClusterAssignment",
    "ClusterRecord",
    "ConversationMessage",
    "ConversationStore",
    "DreamingError",
    "DreamingPipeline",
    "EmbeddingService",
    "IncrementalClusterer",
    "MemoryEntry",
    "MemoryError",
    "MessageRole",
]
