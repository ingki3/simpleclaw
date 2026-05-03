"""DreamingPipeline Phase 3 (그래프형 클러스터 드리밍) 단위 테스트.

검증 범위:
- assign_clusters_for_unprocessed: 새 임베딩 메시지를 기존 클러스터에 부착하거나 신규 생성
- assign_clusters_for_unprocessed: enable_clusters=False면 빈 dict 반환(레거시 모드)
- summarize_cluster: LLM 모킹 시 JSON 라벨/요약 추출, 폴백 시 단순 bullet 요약
- upsert_memory_section: 마커 사이 본문만 교체, 외부 영역 보존
- run() 통합: 클러스터 모드 활성 시 MEMORY.md에 마커 섹션이 생기고 USER/SOUL/AGENT는 별도 갱신
- run() 레거시: enable_clusters=False면 기존 append 동작 그대로
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.memory.clustering import IncrementalClusterer
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.dreaming import DreamingPipeline
from simpleclaw.memory.models import ConversationMessage, MessageRole


@pytest.fixture
def setup(tmp_path):
    """클러스터 모드 활성화된 파이프라인 + LLM 라우터 비활성(폴백).

    BIZ-72: 클러스터 모드는 MEMORY.md의 ``managed:dreaming:clusters`` 컨테이너 안쪽에서만
    cluster 섹션을 upsert한다. USER.md도 ``insights`` managed 섹션을 갖춰야 dreaming이
    fail-closed 없이 진행된다.
    """
    db = tmp_path / "test.db"
    store = ConversationStore(db)
    memory_file = tmp_path / "MEMORY.md"
    memory_file.write_text(
        "# Memory\n"
        "\n"
        "Existing content.\n"
        "\n"
        "<!-- managed:dreaming:clusters -->\n"
        "<!-- /managed:dreaming:clusters -->\n"
    )
    user_file = tmp_path / "USER.md"
    user_file.write_text(
        "# User Profile\n"
        "\n"
        "<!-- managed:dreaming:insights -->\n"
        "<!-- /managed:dreaming:insights -->\n"
    )

    clusterer = IncrementalClusterer(threshold=0.75)
    pipeline = DreamingPipeline(
        store,
        memory_file,
        user_file=user_file,
        clusterer=clusterer,
        enable_clusters=True,
    )
    return store, pipeline, memory_file, user_file


def _add_msg_with_embedding(store, content, vec, role=MessageRole.USER):
    mid = store.add_message(ConversationMessage(role=role, content=content))
    store.add_embedding(mid, vec)
    return mid


class TestAssignClustersForUnprocessed:
    def test_creates_first_cluster(self, setup):
        store, pipeline, _, _ = setup
        _add_msg_with_embedding(store, "macbook pro 살까?", [1.0, 0.0, 0.0])

        affected = pipeline.assign_clusters_for_unprocessed()
        assert len(affected) == 1
        cid = next(iter(affected))
        assert affected[cid][0].content == "macbook pro 살까?"

        clusters = store.list_clusters()
        assert len(clusters) == 1
        assert clusters[0].member_count == 1

    def test_attaches_similar_to_existing(self, setup):
        store, pipeline, _, _ = setup
        _add_msg_with_embedding(store, "msg1", [1.0, 0.0, 0.0])
        _add_msg_with_embedding(store, "msg2", [0.95, 0.05, 0.0])  # 유사

        affected = pipeline.assign_clusters_for_unprocessed()
        assert len(affected) == 1  # 둘 다 같은 클러스터
        cid = next(iter(affected))
        assert len(affected[cid]) == 2
        assert store.list_clusters()[0].member_count == 2

    def test_creates_new_cluster_when_dissimilar(self, setup):
        store, pipeline, _, _ = setup
        _add_msg_with_embedding(store, "topic A", [1.0, 0.0, 0.0])
        _add_msg_with_embedding(store, "topic B", [0.0, 1.0, 0.0])  # 직교 → 신규

        affected = pipeline.assign_clusters_for_unprocessed()
        assert len(affected) == 2
        clusters = store.list_clusters()
        assert len(clusters) == 2
        assert all(c.member_count == 1 for c in clusters)

    def test_disabled_returns_empty(self, tmp_path):
        store = ConversationStore(tmp_path / "x.db")
        # enable_clusters=False (기본값)
        pipeline = DreamingPipeline(store, tmp_path / "MEMORY.md")
        _add_msg_with_embedding(store, "msg", [1.0, 0.0])

        assert pipeline.assign_clusters_for_unprocessed() == {}

    def test_empty_db(self, setup):
        _, pipeline, _, _ = setup
        assert pipeline.assign_clusters_for_unprocessed() == {}

    def test_skips_already_clustered(self, setup):
        store, pipeline, _, _ = setup
        _add_msg_with_embedding(store, "msg1", [1.0, 0.0, 0.0])
        # 첫 번째 호출 — 클러스터 생성
        pipeline.assign_clusters_for_unprocessed()
        # 두 번째 호출 — 더 이상 unclustered가 없으므로 빈 결과
        affected = pipeline.assign_clusters_for_unprocessed()
        assert affected == {}


class TestSummarizeCluster:
    @pytest.mark.asyncio
    async def test_fallback_without_router(self, setup):
        _, pipeline, _, _ = setup
        msgs = [
            ConversationMessage(role=MessageRole.USER, content="맥북 프로 사고싶다"),
            ConversationMessage(role=MessageRole.ASSISTANT, content="예산이 얼마인가요?"),
        ]
        result = await pipeline.summarize_cluster(msgs)
        assert "label" in result
        assert "summary" in result
        assert result["label"]  # 비어있지 않음
        assert "맥북" in result["summary"] or "예산" in result["summary"]

    @pytest.mark.asyncio
    async def test_fallback_preserves_existing_label(self, setup):
        _, pipeline, _, _ = setup
        msgs = [ConversationMessage(role=MessageRole.USER, content="x")]
        result = await pipeline.summarize_cluster(
            msgs, existing_label="my-topic", existing_summary="- old fact"
        )
        assert result["label"] == "my-topic"
        # 기존 요약이 보존되고 새 항목이 추가됨
        assert "old fact" in result["summary"]

    @pytest.mark.asyncio
    async def test_with_llm_json(self, setup):
        _, pipeline, _, _ = setup
        mock_response = MagicMock()
        mock_response.text = (
            '{"label": "맥북 구매", "summary": "- 맥북 프로 구매 검토\\n- 예산 협의"}'
        )
        mock_router = MagicMock()
        mock_router.send = AsyncMock(return_value=mock_response)
        pipeline._router = mock_router

        msgs = [ConversationMessage(role=MessageRole.USER, content="msg")]
        result = await pipeline.summarize_cluster(msgs)
        assert result["label"] == "맥북 구매"
        assert "예산 협의" in result["summary"]

    @pytest.mark.asyncio
    async def test_empty_messages_returns_existing(self, setup):
        _, pipeline, _, _ = setup
        result = await pipeline.summarize_cluster(
            [], existing_label="L", existing_summary="S"
        )
        assert result == {"label": "L", "summary": "S"}

    def test_parse_cluster_result_valid(self, setup):
        _, pipeline, _, _ = setup
        result = pipeline._parse_cluster_result(
            '{"label": "abc", "summary": "- x"}', "old", "old summary"
        )
        assert result["label"] == "abc"
        assert result["summary"] == "- x"

    def test_parse_cluster_result_code_block(self, setup):
        _, pipeline, _, _ = setup
        result = pipeline._parse_cluster_result(
            '```json\n{"label": "z", "summary": "- y"}\n```', "", ""
        )
        assert result["label"] == "z"

    def test_parse_cluster_result_invalid_json(self, setup):
        _, pipeline, _, _ = setup
        result = pipeline._parse_cluster_result(
            "not json", "fallback-label", "fallback-summary"
        )
        assert result["label"] == "fallback-label"
        # 기존 요약을 우선 보존
        assert result["summary"] == "fallback-summary"


class TestUpsertMemorySection:
    def test_creates_new_section(self, setup):
        _, pipeline, memory_file, _ = setup
        pipeline.upsert_memory_section(7, "맥북 구매", "- bullet 1")
        content = memory_file.read_text()
        assert "<!-- cluster:7 start -->" in content
        assert "<!-- cluster:7 end -->" in content
        assert "맥북 구매" in content
        assert "bullet 1" in content
        # 기존 영역 보존
        assert "Existing content" in content

    def test_replaces_existing_section_only(self, setup):
        _, pipeline, memory_file, _ = setup
        # 첫 upsert
        pipeline.upsert_memory_section(7, "old-label", "- old item")
        # 동일 cluster_id로 두 번째 upsert
        pipeline.upsert_memory_section(7, "new-label", "- new item")
        content = memory_file.read_text()
        assert "new-label" in content
        assert "new item" in content
        assert "old-label" not in content
        assert "old item" not in content
        # 마커는 한 쌍만 존재
        assert content.count("<!-- cluster:7 start -->") == 1

    def test_separate_cluster_ids_coexist(self, setup):
        _, pipeline, memory_file, _ = setup
        pipeline.upsert_memory_section(1, "topic-a", "- a")
        pipeline.upsert_memory_section(2, "topic-b", "- b")
        # 1번을 다시 갱신해도 2번은 유지
        pipeline.upsert_memory_section(1, "topic-a-v2", "- a2")
        content = memory_file.read_text()
        assert "topic-a-v2" in content
        assert "topic-b" in content

    def test_fails_closed_when_file_missing(self, tmp_path):
        """BIZ-72: 파일이 없으면 fail-closed — 자동 생성하지 않는다.

        AGENT.md 30→2줄 사고처럼 "자동 생성"이 결국 destructive overwrite로 이어지는
        리스크를 차단한다. 운영자는 명시적으로 템플릿을 두어야 한다.
        """
        from simpleclaw.memory.protected_section import ProtectedSectionMissing

        store = ConversationStore(tmp_path / "x.db")
        memory_file = tmp_path / "new" / "MEMORY.md"
        clusterer = IncrementalClusterer()
        pipeline = DreamingPipeline(
            store, memory_file, clusterer=clusterer, enable_clusters=True
        )
        with pytest.raises(ProtectedSectionMissing):
            pipeline.upsert_memory_section(1, "x", "- y")
        # 파일도 디렉토리도 만들지 않는다
        assert not memory_file.exists()


class TestRunIntegration:
    @pytest.mark.asyncio
    async def test_run_legacy_mode_appends(self, tmp_path):
        """enable_clusters=False면 기존 append 동작 유지(managed:journal 안쪽으로)."""
        store = ConversationStore(tmp_path / "x.db")
        memory_file = tmp_path / "MEMORY.md"
        memory_file.write_text(
            "# Memory\n"
            "\n"
            "<!-- managed:dreaming:journal -->\n"
            "<!-- /managed:dreaming:journal -->\n"
        )
        pipeline = DreamingPipeline(store, memory_file)
        store.add_message(ConversationMessage(role=MessageRole.USER, content="hello"))

        result = await pipeline.run()
        assert result is not None
        content = memory_file.read_text()
        # 클러스터 마커가 없어야 함
        assert "<!-- cluster:" not in content

    @pytest.mark.asyncio
    async def test_run_cluster_mode_writes_markers(self, setup):
        """enable_clusters=True + 임베딩 부착된 메시지가 있으면 마커 섹션 생성."""
        store, pipeline, memory_file, _ = setup
        _add_msg_with_embedding(store, "macbook 살까?", [1.0, 0.0, 0.0])
        _add_msg_with_embedding(store, "예산은?", [0.95, 0.05, 0.0])

        result = await pipeline.run()
        assert result is not None

        content = memory_file.read_text()
        assert "<!-- cluster:" in content
        # 기존 콘텐츠는 보존
        assert "Existing content" in content
        # 클러스터 1개 생성
        assert len(store.list_clusters()) == 1

    @pytest.mark.asyncio
    async def test_run_cluster_mode_with_llm(self, setup):
        store, pipeline, memory_file, user_file = setup

        # LLM router 모킹 — _DREAMING_PROMPT용과 _CLUSTER_SUMMARY_PROMPT용 두 종류 응답
        # send가 호출 순서대로 응답하도록 side_effect 사용
        dreaming_resp = MagicMock()
        dreaming_resp.text = (
            '{"memory": "## today\\n- talked", "user_insights": "- likes mac",'
            ' "soul_updates": "", "agent_updates": ""}'
        )
        cluster_resp = MagicMock()
        cluster_resp.text = '{"label": "맥북", "summary": "- 맥북 구매 논의"}'

        mock_router = MagicMock()
        mock_router.send = AsyncMock(side_effect=[dreaming_resp, cluster_resp])
        pipeline._router = mock_router

        _add_msg_with_embedding(store, "macbook?", [1.0, 0.0])

        result = await pipeline.run()
        assert result is not None

        content = memory_file.read_text()
        assert "맥북 구매 논의" in content
        # 시간순 append 본문은 클러스터 모드에서 작성하지 않음
        assert "talked" not in content
        # USER.md는 갱신되어야 함
        user_content = user_file.read_text()
        assert "likes mac" in user_content

    @pytest.mark.asyncio
    async def test_run_no_messages(self, setup):
        _, pipeline, _, _ = setup
        result = await pipeline.run()
        assert result is None

    @pytest.mark.asyncio
    async def test_run_cluster_mode_without_embeddings_falls_through(self, setup):
        """클러스터 모드라도 임베딩이 없으면 영향받은 클러스터가 0개여야 한다."""
        store, pipeline, memory_file, _ = setup
        store.add_message(ConversationMessage(role=MessageRole.USER, content="x"))
        # 임베딩 부착하지 않음

        await pipeline.run()
        # USER 등은 폴백으로 채워질 수 있으므로 result는 신경 쓰지 않는다.
        # 핵심: 클러스터 마커는 안 생긴다.
        content = memory_file.read_text()
        assert "<!-- cluster:" not in content
        assert store.list_clusters() == []
