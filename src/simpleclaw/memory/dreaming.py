"""드리밍 파이프라인: 대화 이력을 요약하여 핵심 기억(MEMORY.md)과 사용자 프로필(USER.md)을 갱신하는 모듈.

주요 동작 흐름:
1. run() 호출 시 기존 MEMORY.md / USER.md를 백업(.bak)한다.
2. 마지막 드리밍 이후 미처리 대화 메시지를 수집한다.
3. LLM에게 대화를 분석시켜 기억 요약(memory)과 사용자 인사이트(user_insights)를 추출한다.
4. 결과를 각각 MEMORY.md, USER.md에 추가(append)한다.

설계 결정:
- LLM 호출 실패 시 단순 텍스트 요약(fallback)으로 대체하여 파이프라인이 중단되지 않도록 한다.
- 대화 텍스트는 8000자로 잘라 LLM 컨텍스트 초과를 방지한다.
- 백업 파일명에 타임스탬프를 포함하여 여러 번 드리밍해도 이전 백업이 덮어씌워지지 않는다.
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
다음 대화 내역을 분석하여 네 가지를 JSON으로 추출하세요.

1. "memory": 오늘 있었던 사실, 이벤트, 결정 사항을 bullet point로 요약
   - 날짜 헤더 포함 (## {date} 형식)
   - 사실 기반만 (의견/추측 금지)
   - 반복되는 주제나 관심사를 기록 (패턴 파악용)

2. "user_insights": 사용자에 대해 새로 알게 된 정보 (선호도, 관심사, 습관)
   - 이미 알고 있는 정보(기존 USER.md 내용)는 제외
   - 추측이 아닌 대화에서 명확히 드러난 정보만
   - 민감한 개인정보(비밀번호, 금융정보)는 절대 저장하지 않음
   - 없으면 빈 문자열

3. "soul_updates": 에이전트의 성격·말투·호칭에 대한 사용자의 피드백
   - 사용자가 명시적으로 요청한 변경만 (예: "반말 써", "이모지 쓰지 마", "~라고 불러")
   - 기존 SOUL.md 내용과 중복이면 제외
   - 추측하지 말고, 사용자가 직접 지시한 것만 포함
   - 없으면 빈 문자열

4. "agent_updates": 에이전트 행동 규칙에 대한 사용자의 피드백
   - 사용자가 명시적으로 요청한 설정 변경만 (예: 캘린더 추가, 스킬 설정 등)
   - 기존 AGENT.md 내용과 중복이면 제외
   - 없으면 빈 문자열

## 기존 SOUL.md 내용
{existing_soul_md}

## 기존 AGENT.md 내용
{existing_agent_md}

## 기존 USER.md 내용
{existing_user_md}

## 대화 내역
{conversations}

JSON 형식으로만 응답하세요:
{{"memory": "## {date}\\n- 항목1\\n- 항목2", "user_insights": "- 새 정보1", "soul_updates": "- 변경1", "agent_updates": "- 변경1"}}"""


class DreamingPipeline:
    """대화 이력을 분석하여 MEMORY.md, USER.md, SOUL.md, AGENT.md를 갱신하는 파이프라인.

    LLM을 사용해 대화를 분석하고, 각 파일의 역할에 맞는 정보를 추출·갱신한다.
    파일 수정 전 memory-backup/ 폴더에 .bak 백업을 생성하여 데이터 손실을 방지한다.
    """

    def __init__(
        self,
        conversation_store: ConversationStore,
        memory_file: str | Path,
        user_file: str | Path | None = None,
        soul_file: str | Path | None = None,
        agent_file: str | Path | None = None,
        llm_router=None,
        dreaming_model: str = "",
    ) -> None:
        """드리밍 파이프라인을 초기화한다.

        Args:
            conversation_store: 대화 이력 저장소 인스턴스.
            memory_file: MEMORY.md 파일 경로.
            user_file: USER.md 파일 경로. None이면 사용자 인사이트를 저장하지 않는다.
            soul_file: SOUL.md 파일 경로. None이면 성격/말투 갱신을 하지 않는다.
            agent_file: AGENT.md 파일 경로. None이면 행동 규칙 갱신을 하지 않는다.
            llm_router: LLM 호출을 위한 라우터. None이면 폴백 요약을 사용한다.
            dreaming_model: 드리밍에 사용할 LLM 모델명. 빈 문자열이면 라우터 기본값 사용.
        """
        self._store = conversation_store
        self._memory_file = Path(memory_file)
        self._user_file = Path(user_file) if user_file else None
        self._soul_file = Path(soul_file) if soul_file else None
        self._agent_file = Path(agent_file) if agent_file else None
        self._router = llm_router
        self._dreaming_model = dreaming_model or None

    def create_backup(self, file_path: Path, max_backups: int = 3) -> Path | None:
        """파일 수정 전 타임스탬프가 포함된 .bak 백업을 생성한다.

        백업은 원본 파일의 부모 디렉토리 하위 memory-backup/ 폴더에 저장된다.
        최근 max_backups개만 유지하고 오래된 백업은 자동 삭제한다.

        Args:
            file_path: 백업할 원본 파일 경로.
            max_backups: 유지할 최대 백업 개수 (기본 3).

        Returns:
            생성된 백업 파일 경로. 원본 파일이 없으면 None.
        """
        if not file_path.is_file():
            return None

        backup_dir = file_path.parent / "memory-backup"
        backup_dir.mkdir(parents=True, exist_ok=True)

        backup_name = f"{file_path.stem}.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
        backup_path = backup_dir / backup_name
        shutil.copy2(file_path, backup_path)
        logger.info("Created backup: %s", backup_path)

        # 오래된 백업 정리: 같은 stem의 최근 max_backups개만 유지
        stem = file_path.stem
        existing_backups = sorted(
            backup_dir.glob(f"{stem}.*.bak"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old_backup in existing_backups[max_backups:]:
            old_backup.unlink()
            logger.debug("Removed old backup: %s", old_backup)

        return backup_path

    def collect_unprocessed(self, last_dreaming: datetime | None = None) -> list:
        """마지막 드리밍 이후 미처리 대화 메시지를 수집한다.

        Args:
            last_dreaming: 마지막 드리밍 시각. None이면 최근 50개 메시지를 가져온다.

        Returns:
            처리 대상 ConversationMessage 리스트.
        """
        if last_dreaming:
            return self._store.get_since(last_dreaming)
        return self._store.get_recent(limit=50)

    async def summarize(self, messages: list) -> dict:
        """LLM을 사용하여 대화 요약을 생성한다.

        LLM 호출이 실패하거나 라우터가 없으면 단순 텍스트 요약으로 폴백한다.

        Args:
            messages: 요약 대상 대화 메시지 리스트.

        Returns:
            'memory'와 'user_insights' 키를 포함하는 딕셔너리.
        """
        if not messages:
            return {"memory": "", "user_insights": ""}

        if self._router:
            try:
                return await self._summarize_with_llm(messages)
            except Exception:
                logger.exception("LLM summarization failed, using fallback")

        return {"memory": self._summarize_fallback(messages), "user_insights": ""}

    async def _summarize_with_llm(self, messages: list) -> dict:
        """LLM을 호출하여 대화를 분석하고 memory/user/soul/agent 업데이트를 추출한다."""
        from simpleclaw.llm.models import LLMRequest

        existing_user_md = ""
        if self._user_file and self._user_file.is_file():
            existing_user_md = self._user_file.read_text(encoding="utf-8")

        existing_soul_md = ""
        if self._soul_file and self._soul_file.is_file():
            existing_soul_md = self._soul_file.read_text(encoding="utf-8")

        existing_agent_md = ""
        if self._agent_file and self._agent_file.is_file():
            existing_agent_md = self._agent_file.read_text(encoding="utf-8")

        conv_lines = []
        for msg in messages:
            role = msg.role.value.upper()
            conv_lines.append(f"[{role}] {msg.content}")
        # LLM 컨텍스트 윈도우 초과를 방지하기 위해 8000자로 제한
        conversations = "\n".join(conv_lines)[:8000]

        date_str = datetime.now().strftime("%Y-%m-%d")
        prompt = _DREAMING_PROMPT.format(
            existing_soul_md=existing_soul_md or "(없음)",
            existing_agent_md=existing_agent_md or "(없음)",
            existing_user_md=existing_user_md or "(없음)",
            conversations=conversations,
            date=date_str,
        )

        request = LLMRequest(
            system_prompt="You are a conversation analyzer. Respond with valid JSON only.",
            user_message=prompt,
            backend_name=self._dreaming_model,
        )
        response = await self._router.send(request)
        return self._parse_llm_result(response.text.strip())

    def _parse_llm_result(self, raw: str) -> dict:
        """LLM의 JSON 응답을 파싱하여 memory/user/soul/agent 업데이트를 추출한다.

        LLM이 마크다운 코드 블록으로 감싼 경우에도 처리할 수 있다.
        JSON 파싱 실패 시 원본 텍스트 앞 500자를 memory로 사용한다.
        """
        # LLM이 ```json ... ``` 형태로 감싸는 경우 코드 블록 내용만 추출
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
                "soul_updates": result.get("soul_updates", ""),
                "agent_updates": result.get("agent_updates", ""),
            }
        except json.JSONDecodeError:
            logger.warning("Failed to parse dreaming JSON: %s", raw[:200])
            return {"memory": raw[:500], "user_insights": "", "soul_updates": "", "agent_updates": ""}

    def _summarize_fallback(self, messages: list) -> str:
        """LLM 없이 단순 텍스트 기반 요약을 생성한다. 각 메시지의 첫 5단어를 토픽으로 추출."""
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
        """드리밍 요약을 MEMORY.md 파일 끝에 추가한다.

        파일이 없으면 '# Memory' 헤더와 함께 새로 생성한다.
        """
        if not summary:
            return

        self._memory_file.parent.mkdir(parents=True, exist_ok=True)

        existing = ""
        if self._memory_file.is_file():
            existing = self._memory_file.read_text(encoding="utf-8")

        if not existing.strip():
            existing = "# Memory\n"

        if not existing.endswith("\n"):
            existing += "\n"

        new_content = f"{existing}\n{summary}\n"
        self._memory_file.write_text(new_content, encoding="utf-8")
        logger.info("Updated memory file: %s", self._memory_file)

    def _update_file_section(self, file_path: Path, updates: str, section_header: str) -> None:
        """파일에 날짜별 섹션 헤더와 함께 업데이트 내용을 추가한다.

        파일이 없거나 updates가 비어있으면 아무 작업도 하지 않는다.
        """
        if not updates or not file_path:
            return

        file_path.parent.mkdir(parents=True, exist_ok=True)

        existing = ""
        if file_path.is_file():
            existing = file_path.read_text(encoding="utf-8")

        if not existing.strip():
            existing = f"# {file_path.stem}\n"

        if not existing.endswith("\n"):
            existing += "\n"

        date_str = datetime.now().strftime("%Y-%m-%d")
        new_content = f"{existing}\n## {section_header} ({date_str})\n{updates}\n"
        file_path.write_text(new_content, encoding="utf-8")
        logger.info("Updated file: %s", file_path)

    def update_user_file(self, insights: str) -> None:
        """새로운 사용자 인사이트를 USER.md 파일에 추가한다."""
        if self._user_file:
            self._update_file_section(self._user_file, insights, "Dreaming Insights")

    def update_soul_file(self, updates: str) -> None:
        """에이전트 성격/말투 변경을 SOUL.md 파일에 추가한다."""
        if self._soul_file:
            self._update_file_section(self._soul_file, updates, "Dreaming Updates")

    def update_agent_file(self, updates: str) -> None:
        """에이전트 행동 규칙 변경을 AGENT.md 파일에 추가한다."""
        if self._agent_file:
            self._update_file_section(self._agent_file, updates, "Dreaming Updates")

    async def run(self, last_dreaming: datetime | None = None) -> MemoryEntry | None:
        """전체 드리밍 파이프라인을 실행한다.

        1. 미처리 대화 메시지를 수집한다.
        2. 처리할 내용이 있으면 대상 파일들을 백업한다.
        3. LLM을 통해 요약을 생성한다.
        4. 각 파일에 해당하는 내용을 추가한다:
           - MEMORY.md: 오늘의 사실/이벤트
           - USER.md: 사용자 인사이트
           - SOUL.md: 성격/말투 변경
           - AGENT.md: 행동 규칙 변경

        Args:
            last_dreaming: 마지막 드리밍 시각. None이면 최근 메시지를 대상으로 한다.

        Returns:
            생성된 MemoryEntry 객체. 처리할 메시지가 없거나 결과가 비어있으면 None.
        """
        messages = self.collect_unprocessed(last_dreaming)
        if not messages:
            logger.info("No new messages to process for dreaming.")
            return None

        # 처리할 메시지가 있을 때만 백업 생성
        self.create_backup(self._memory_file)
        if self._user_file:
            self.create_backup(self._user_file)
        if self._soul_file:
            self.create_backup(self._soul_file)
        if self._agent_file:
            self.create_backup(self._agent_file)

        result = await self.summarize(messages)
        memory_summary = result.get("memory", "")
        user_insights = result.get("user_insights", "")
        soul_updates = result.get("soul_updates", "")
        agent_updates = result.get("agent_updates", "")

        if not any([memory_summary, user_insights, soul_updates, agent_updates]):
            return None

        if memory_summary:
            self.append_to_memory(memory_summary)
        if user_insights:
            self.update_user_file(user_insights)
        if soul_updates:
            self.update_soul_file(soul_updates)
        if agent_updates:
            self.update_agent_file(agent_updates)

        return MemoryEntry(
            summary=memory_summary,
            source=f"dreaming_{datetime.now().strftime('%Y-%m-%d')}",
        )
