"""Tests for the dreaming pipeline.

BIZ-72: 모든 fixture는 Protected Section 마커를 포함한다. 마커가 없는 파일에 대한
쓰기는 fail-closed로 차단되며, 그 동작 자체는 ``test_dreaming_protected_section.py``에서
별도로 검증한다.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.dreaming import DreamingPipeline
from simpleclaw.memory.models import ConversationMessage, MessageRole


class TestDreamingPipeline:
    @pytest.fixture
    def setup(self, tmp_path):
        db = tmp_path / "test.db"
        store = ConversationStore(db)
        memory_file = tmp_path / "MEMORY.md"
        # BIZ-72: managed 섹션 마커 안쪽이 dreaming의 쓰기 영역. "Existing content"는
        # 마커 외부의 사용자 콘텐츠 — 어떤 dreaming 호출에서도 보존되어야 한다.
        memory_file.write_text(
            "# Core Memory\n"
            "\n"
            "Existing content.\n"
            "\n"
            "<!-- managed:dreaming:journal -->\n"
            "<!-- /managed:dreaming:journal -->\n"
        )
        user_file = tmp_path / "USER.md"
        user_file.write_text(
            "# User Profile\n"
            "\n"
            "## Preferences\n"
            "- Language: Korean\n"
            "\n"
            "<!-- managed:dreaming:insights -->\n"
            "<!-- /managed:dreaming:insights -->\n"
        )
        # BIZ-79: 새 기본은 dry_run(큐로 적재). 본 모듈의 기존 테스트들은 sidecar 직접
        # 적재 경로(legacy)를 가드하므로 명시적으로 dry_run_enabled=False 로 옵트아웃한다.
        pipeline = DreamingPipeline(
            store, memory_file, user_file=user_file, dry_run_enabled=False
        )
        return store, pipeline, memory_file, user_file

    def test_create_backup(self, setup):
        _, pipeline, memory_file, _ = setup
        backup = pipeline.create_backup(memory_file)
        assert backup is not None
        assert backup.exists()
        assert backup.read_text() == memory_file.read_text()

    def test_create_backup_no_file(self, tmp_path):
        store = ConversationStore(tmp_path / "test.db")
        pipeline = DreamingPipeline(store, tmp_path / "nonexistent.md")
        assert pipeline.create_backup(tmp_path / "nonexistent.md") is None

    @pytest.mark.asyncio
    async def test_summarize_fallback(self, setup):
        """Without LLM router, fallback summary is used."""
        _, pipeline, _, _ = setup
        messages = [
            ConversationMessage(role=MessageRole.USER, content="What is the weather?"),
            ConversationMessage(role=MessageRole.ASSISTANT, content="It is sunny."),
        ]
        result = await pipeline.summarize(messages)
        assert "memory" in result
        assert len(result["memory"]) > 0

    @pytest.mark.asyncio
    async def test_summarize_empty(self, setup):
        _, pipeline, _, _ = setup
        result = await pipeline.summarize([])
        assert result["memory"] == ""
        assert result["user_insights"] == ""

    def test_append_to_memory(self, setup):
        _, pipeline, memory_file, _ = setup
        pipeline.append_to_memory("## New Summary\n\n- Item 1")
        content = memory_file.read_text()
        assert "Existing content" in content
        assert "New Summary" in content

    def test_update_user_file(self, setup):
        _, pipeline, _, user_file = setup
        pipeline.update_user_file("- Likes KBO baseball")
        content = user_file.read_text()
        assert "Language: Korean" in content
        assert "Likes KBO baseball" in content
        assert "Dreaming Insights" in content

    def test_update_user_file_empty(self, setup):
        _, pipeline, _, user_file = setup
        original = user_file.read_text()
        pipeline.update_user_file("")
        assert user_file.read_text() == original

    @pytest.mark.asyncio
    async def test_run_full_pipeline_fallback(self, setup):
        """Full pipeline without LLM (fallback)."""
        store, pipeline, memory_file, _ = setup
        store.add_message(ConversationMessage(
            role=MessageRole.USER, content="Plan my day"
        ))
        store.add_message(ConversationMessage(
            role=MessageRole.ASSISTANT, content="Here is your schedule"
        ))

        result = await pipeline.run()
        assert result is not None
        assert "dreaming" in result.source

        content = memory_file.read_text()
        assert "Plan my" in content or "Here is" in content

    @pytest.mark.asyncio
    async def test_run_no_messages(self, setup):
        _, pipeline, _, _ = setup
        result = await pipeline.run()
        assert result is None

    @pytest.mark.asyncio
    async def test_run_with_llm(self, setup):
        """Full pipeline with mocked LLM."""
        store, pipeline, memory_file, user_file = setup

        # Mock LLM router
        mock_response = MagicMock()
        mock_response.text = '{"memory": "## 2026-04-24\\n- Planned the day\\n- Checked weather", "user_insights": "- Interested in daily planning"}'
        mock_router = MagicMock()
        mock_router.send = AsyncMock(return_value=mock_response)
        pipeline._router = mock_router

        store.add_message(ConversationMessage(
            role=MessageRole.USER, content="Plan my day"
        ))
        store.add_message(ConversationMessage(
            role=MessageRole.ASSISTANT, content="Here is your schedule"
        ))

        result = await pipeline.run()
        assert result is not None

        # MEMORY.md updated
        memory_content = memory_file.read_text()
        assert "Planned the day" in memory_content

        # USER.md updated
        user_content = user_file.read_text()
        assert "daily planning" in user_content

    @pytest.mark.asyncio
    async def test_llm_model_routing(self, setup):
        """Dreaming model is passed to LLM request."""
        store, pipeline, _, _ = setup
        pipeline._dreaming_model = "gemini"

        mock_response = MagicMock()
        mock_response.text = '{"memory": "## test", "user_insights": ""}'
        mock_router = MagicMock()
        mock_router.send = AsyncMock(return_value=mock_response)
        pipeline._router = mock_router

        store.add_message(ConversationMessage(
            role=MessageRole.USER, content="Hello"
        ))

        await pipeline.run()

        # Verify the request used the dreaming model
        call_args = mock_router.send.call_args[0][0]
        assert call_args.backend_name == "gemini"

    def test_parse_llm_result_valid(self, setup):
        _, pipeline, _, _ = setup
        result = pipeline._parse_llm_result(
            '{"memory": "## 2026-04-24\\n- item", "user_insights": "- new info"}'
        )
        assert "item" in result["memory"]
        assert "new info" in result["user_insights"]

    def test_parse_llm_result_code_block(self, setup):
        _, pipeline, _, _ = setup
        result = pipeline._parse_llm_result(
            '```json\n{"memory": "test", "user_insights": ""}\n```'
        )
        assert result["memory"] == "test"

    def test_parse_llm_result_invalid(self, setup):
        _, pipeline, _, _ = setup
        result = pipeline._parse_llm_result("not json at all")
        assert "memory" in result
        assert result["memory"] == "not json at all"

    @pytest.mark.asyncio
    async def test_run_persists_insight_meta_sidecar(self, setup):
        """BIZ-73: dreaming.run() 이 user_insights_meta 를 sidecar 에 영속화한다.

        같은 topic 의 인사이트가 두 회차에 걸쳐 들어오면 evidence_count 가 누적되고,
        승격 임계치(기본 3)에 도달하기 전까지 confidence 는 0.4~0.7 사이를 유지한다.
        """
        from simpleclaw.memory.insights import InsightStore

        store, pipeline, _, user_file = setup

        mock_response = MagicMock()
        mock_response.text = (
            '{"memory": "## d\\n- x", "user_insights": "- 정치 뉴스 관심", '
            '"user_insights_meta": [{"topic": "정치뉴스", "text": "정치 뉴스 관심"}], '
            '"soul_updates": "", "agent_updates": ""}'
        )
        mock_router = MagicMock()
        mock_router.send = AsyncMock(return_value=mock_response)
        pipeline._router = mock_router

        # 회차 1
        store.add_message(ConversationMessage(role=MessageRole.USER, content="뉴스"))
        await pipeline.run()

        sidecar = InsightStore(user_file.parent / "insights.jsonl")
        loaded = sidecar.load()
        assert "정치뉴스" in loaded
        assert loaded["정치뉴스"].evidence_count == 1
        # DoD #1: 단발 관측은 0.4 캡.
        assert loaded["정치뉴스"].confidence == 0.4

        # 회차 2 — 같은 topic 이 다시 들어오면 evidence_count 누적.
        store.add_message(ConversationMessage(role=MessageRole.USER, content="뉴스 또"))
        await pipeline.run()

        loaded = sidecar.load()
        assert loaded["정치뉴스"].evidence_count == 2
        # 2/3 보간 → 0.55. 아직 승격 전.
        assert loaded["정치뉴스"].confidence == pytest.approx(0.55)

    @pytest.mark.asyncio
    async def test_run_records_source_msg_ids_for_new_and_reinforced_insights(
        self, setup
    ):
        """BIZ-77 DoD #3 — 신규/재강화 인사이트 모두에 대해 source 가 누락 없이 기록된다.

        회차 1: 메시지 1건만 있는 상태에서 신규 인사이트가 생성되면 그 메시지의
        rowid 가 ``source_msg_ids`` 와 ``start_msg_id``/``end_msg_id`` 에 그대로 들어간다.

        회차 2: 같은 topic 으로 재강화되면 새 메시지의 rowid 가 누적되고 범위가
        넓어진다(end_msg_id 가 새 메시지 id 로 끌어올려진다). 회차별로 source 가
        분실되거나 덮어써지지 않아야 한다 — 이 테스트의 핵심.
        """
        from simpleclaw.memory.insights import InsightStore

        store, pipeline, _, user_file = setup

        mock_response = MagicMock()
        mock_response.text = (
            '{"memory": "## d\\n- x", "user_insights": "- 정치 뉴스 관심", '
            '"user_insights_meta": [{"topic": "정치뉴스", "text": "정치 뉴스 관심"}], '
            '"soul_updates": "", "agent_updates": ""}'
        )
        mock_router = MagicMock()
        mock_router.send = AsyncMock(return_value=mock_response)
        pipeline._router = mock_router

        # 회차 1 — 메시지 1건, 신규 인사이트.
        first_id = store.add_message(
            ConversationMessage(role=MessageRole.USER, content="뉴스 보여줘")
        )
        await pipeline.run()

        sidecar = InsightStore(user_file.parent / "insights.jsonl")
        loaded = sidecar.load()
        assert "정치뉴스" in loaded
        meta = loaded["정치뉴스"]
        # 신규 인사이트 → 이번 회차의 모든 메시지 id 가 source 로 박힌다.
        assert meta.source_msg_ids == [first_id]
        # 단일 메시지이므로 start == end == first_id.
        assert meta.start_msg_id == first_id
        assert meta.end_msg_id == first_id

        # 회차 2 — 새 메시지 1건이 더 추가되면 같은 topic 이 reinforce 되고
        # source_msg_ids 에 둘 다 들어가야 한다(첫 회차 source 가 분실되면 안 됨).
        second_id = store.add_message(
            ConversationMessage(role=MessageRole.USER, content="정치 더 알려줘")
        )
        assert second_id > first_id  # rowid 단조 증가 가정.
        await pipeline.run()

        loaded = sidecar.load()
        meta = loaded["정치뉴스"]
        assert meta.evidence_count == 2
        # 두 회차의 source 가 모두 보존되고, 범위는 첫 메시지 ~ 두 번째 메시지.
        assert first_id in meta.source_msg_ids
        assert second_id in meta.source_msg_ids
        assert meta.start_msg_id == first_id
        assert meta.end_msg_id == second_id

    @pytest.mark.asyncio
    async def test_backup_both_files(self, setup):
        """Both MEMORY.md and USER.md are backed up."""
        store, pipeline, memory_file, user_file = setup
        store.add_message(ConversationMessage(
            role=MessageRole.USER, content="test"
        ))

        await pipeline.run()

        # Check backups exist in memory-backup/ subdirectory
        backup_dir = memory_file.parent / "memory-backup"
        memory_baks = list(backup_dir.glob("MEMORY.*.bak"))
        user_baks = list(backup_dir.glob("USER.*.bak"))
        assert len(memory_baks) >= 1
        assert len(user_baks) >= 1
