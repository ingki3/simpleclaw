"""ConversationStore를 graph credential/config와 격리해 주입하는 adapter."""

from __future__ import annotations

from simpleclaw.memory.models import ConversationMessage, MessageRole

from ..side_effect_monitor import record_shadow_side_effect


class ConversationStorePersistenceAdapter:
    """PersistenceRuntime writer signature를 기존 ConversationStore에 연결한다."""

    def __init__(self, store, *, channel: str | None = None) -> None:
        self._store = store
        self._channel = channel

    def __call__(
        self,
        session_key: str,
        persistence_id: str,
        payload_hash: str,
        content: str,
    ) -> None:
        record_shadow_side_effect("conversation_write")
        self._store.save_outbound_once(
            ConversationMessage(
                role=MessageRole.ASSISTANT,
                content=content,
                channel=self._channel,
            ),
            session_key=session_key,
            persistence_id=persistence_id,
            payload_hash=payload_hash,
        )
