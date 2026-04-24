"""Dreaming pipeline: summarize conversations and update core memory.

Uses an LLM to analyze conversation history and extract:
1. Memory summaries (events, decisions) → MEMORY.md
2. User insights (preferences, interests) → USER.md
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from simpleclaw.memory.models import DreamingError, MemoryEntry
from simpleclaw.memory.conversation_store import ConversationStore

logger = logging.getLogger(__name__)

_DREAMING_PROMPT = """\
다음 대화 내역을 분석하여 두 가지를 JSON으로 추출하세요.

1. "memory": 오늘 있었던 사실, 이벤트, 결정 사항을 bullet point로 요약
   - 날짜 헤더 포함 (## {date} 형식)
   - 사실 기반만 (의견/추측 금지)
   - 반복되는 주제나 관심사를 기록

2. "user_insights": 사용자에 대해 새로 알게 된 정보 (선호도, 관심사, 습관)
   - 이미 알고 있는 정보(아래 기존 USER.md 내용)는 제외
   - 추측이 아닌 대화에서 명확히 드러난 정보만
   - 없으면 빈 문자열

## 기존 USER.md 내용
{existing_user_md}

## 대화 내역
{conversations}

JSON 형식으로만 응답하세요:
{{"memory": "## {date}\\n- 항목1\\n- 항목2", "user_insights": "- 새 정보1\\n- 새 정보2"}}"""


class DreamingPipeline:
    """Processes conversation history into core memory summaries.

    Uses an LLM to analyze conversations and updates both MEMORY.md
    and USER.md. Creates .bak backups before modifying files.
    """

    def __init__(
        self,
        conversation_store: ConversationStore,
        memory_file: str | Path,
        user_file: str | Path | None = None,
        llm_router=None,
        dreaming_model: str = "",
    ) -> None:
        self._store = conversation_store
        self._memory_file = Path(memory_file)
        self._user_file = Path(user_file) if user_file else None
        self._router = llm_router
        self._dreaming_model = dreaming_model or None

    def create_backup(self, file_path: Path) -> Path | None:
        """Create a .bak backup of a file before modification.

        Returns the backup path, or None if the file doesn't exist.
        """
        if not file_path.is_file():
            return None

        backup_path = file_path.with_suffix(
            f".{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
        )
        shutil.copy2(file_path, backup_path)
        logger.info("Created backup: %s", backup_path)
        return backup_path

    def collect_unprocessed(self, last_dreaming: datetime | None = None) -> list:
        """Collect conversation messages since last dreaming session."""
        if last_dreaming:
            return self._store.get_since(last_dreaming)
        return self._store.get_recent(limit=50)

    async def summarize(self, messages: list) -> dict:
        """Generate a summary using LLM.

        Returns dict with 'memory' and 'user_insights' keys.
        Falls back to simple text summary if LLM is unavailable.
        """
        if not messages:
            return {"memory": "", "user_insights": ""}

        # Try LLM-based summarization
        if self._router:
            try:
                return await self._summarize_with_llm(messages)
            except Exception:
                logger.exception("LLM summarization failed, using fallback")

        # Fallback: simple text-based summary
        return {"memory": self._summarize_fallback(messages), "user_insights": ""}

    async def _summarize_with_llm(self, messages: list) -> dict:
        """Call LLM to analyze conversations and extract memory + insights."""
        from simpleclaw.llm.models import LLMRequest

        # Read existing USER.md
        existing_user_md = ""
        if self._user_file and self._user_file.is_file():
            existing_user_md = self._user_file.read_text(encoding="utf-8")

        # Format conversations
        conv_lines = []
        for msg in messages:
            role = msg.role.value.upper()
            conv_lines.append(f"[{role}] {msg.content}")
        conversations = "\n".join(conv_lines)

        date_str = datetime.now().strftime("%Y-%m-%d")
        prompt = _DREAMING_PROMPT.format(
            existing_user_md=existing_user_md or "(없음)",
            conversations=conversations[:8000],
            date=date_str,
        )

        request = LLMRequest(
            system_prompt="You are a conversation analyzer. Respond with valid JSON only.",
            user_message=prompt,
            backend_name=self._dreaming_model,
        )
        response = await self._router.send(request)
        raw = response.text.strip()

        return self._parse_llm_result(raw)

    def _parse_llm_result(self, raw: str) -> dict:
        """Parse LLM JSON response into memory + user_insights."""
        # Handle markdown code blocks
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            result = json.loads(raw)
            return {
                "memory": result.get("memory", ""),
                "user_insights": result.get("user_insights", ""),
            }
        except json.JSONDecodeError:
            logger.warning("Failed to parse dreaming JSON: %s", raw[:200])
            return {"memory": raw[:500], "user_insights": ""}

    def _summarize_fallback(self, messages: list) -> str:
        """Simple text-based summary (no LLM)."""
        lines = []
        date_str = datetime.now().strftime("%Y-%m-%d")
        lines.append(f"## {date_str}")
        lines.append("")

        topics = set()
        for msg in messages:
            words = msg.content.split()[:10]
            if words:
                topics.add(" ".join(words[:5]))

        for topic in list(topics)[:5]:
            lines.append(f"- {topic}...")

        return "\n".join(lines)

    def append_to_memory(self, summary: str) -> None:
        """Append a dreaming summary to MEMORY.md."""
        if not summary:
            return

        self._memory_file.parent.mkdir(parents=True, exist_ok=True)

        existing = ""
        if self._memory_file.is_file():
            existing = self._memory_file.read_text(encoding="utf-8")

        # Add header if file is empty
        if not existing.strip():
            existing = "# Memory\n"

        if not existing.endswith("\n"):
            existing += "\n"

        new_content = f"{existing}\n{summary}\n"
        self._memory_file.write_text(new_content, encoding="utf-8")
        logger.info("Updated memory file: %s", self._memory_file)

    def update_user_file(self, insights: str) -> None:
        """Append new user insights to USER.md."""
        if not insights or not self._user_file:
            return

        self._user_file.parent.mkdir(parents=True, exist_ok=True)

        existing = ""
        if self._user_file.is_file():
            existing = self._user_file.read_text(encoding="utf-8")

        if not existing.strip():
            existing = "# User Profile\n"

        if not existing.endswith("\n"):
            existing += "\n"

        # Append under a "Discovered by Dreaming" section
        new_content = f"{existing}\n## Dreaming Insights ({datetime.now().strftime('%Y-%m-%d')})\n{insights}\n"
        self._user_file.write_text(new_content, encoding="utf-8")
        logger.info("Updated user file: %s", self._user_file)

    async def run(self, last_dreaming: datetime | None = None) -> MemoryEntry | None:
        """Execute the full dreaming pipeline.

        1. Create backups of MEMORY.md and USER.md
        2. Collect unprocessed messages
        3. Generate summary via LLM
        4. Append memory to MEMORY.md
        5. Append user insights to USER.md (if any)
        """
        # Step 1: Backup
        self.create_backup(self._memory_file)
        if self._user_file:
            self.create_backup(self._user_file)

        # Step 2: Collect
        messages = self.collect_unprocessed(last_dreaming)
        if not messages:
            logger.info("No new messages to process for dreaming.")
            return None

        # Step 3: Summarize
        result = await self.summarize(messages)
        memory_summary = result.get("memory", "")
        user_insights = result.get("user_insights", "")

        if not memory_summary and not user_insights:
            return None

        # Step 4: Update MEMORY.md
        if memory_summary:
            self.append_to_memory(memory_summary)

        # Step 5: Update USER.md
        if user_insights:
            self.update_user_file(user_insights)

        return MemoryEntry(
            summary=memory_summary,
            source=f"dreaming_{datetime.now().strftime('%Y-%m-%d')}",
        )
