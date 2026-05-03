"""DreamingPipeline 메트릭 기록 통합 테스트 (BIZ-81).

DoD 검증:
    - 정상 사이클 종료 시 메트릭이 sidecar 에 저장됨
      (started_at, ended_at, input_msg_count, generated_insight_count)
    - 메시지 0건 → skip_reason="no_messages"
    - Protected Section preflight 실패 → skip_reason="preflight_failed"
    - 결과 비어있음 → skip_reason="empty_results"
    - 예외 발생 → error 필드 채워짐
    - 차단된 토픽이 rejected_count 에 카운트됨

run() 의 모든 경로에서 메트릭이 정확히 한 번 기록되며 사이클 자체의 동작은 변하지 않음을 확인.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.dreaming import DreamingPipeline
from simpleclaw.memory.dreaming_runs import (
    SKIP_EMPTY_RESULTS,
    SKIP_NO_MESSAGES,
    SKIP_PREFLIGHT_FAILED,
)
from simpleclaw.memory.models import ConversationMessage, MessageRole


@pytest.fixture
def pipeline_with_runs(tmp_path):
    """파이프라인 + 메트릭 sidecar 가 활성화된 fixture."""
    db = tmp_path / "test.db"
    store = ConversationStore(db)

    memory_file = tmp_path / "MEMORY.md"
    memory_file.write_text(
        "# Core Memory\n"
        "\n"
        "<!-- managed:dreaming:journal -->\n"
        "<!-- /managed:dreaming:journal -->\n"
    )
    user_file = tmp_path / "USER.md"
    user_file.write_text(
        "# User Profile\n"
        "\n"
        "<!-- managed:dreaming:insights -->\n"
        "<!-- /managed:dreaming:insights -->\n"
    )
    runs_file = tmp_path / "dreaming_runs.jsonl"
    pipeline = DreamingPipeline(
        store,
        memory_file,
        user_file=user_file,
        runs_file=runs_file,
    )
    return store, pipeline, memory_file, user_file, runs_file


@pytest.mark.asyncio
async def test_no_messages_records_skip_reason(pipeline_with_runs):
    """대상 메시지가 0건일 때 메트릭이 skip_reason='no_messages' 로 기록되어야 한다."""
    _, pipeline, _, _, runs_file = pipeline_with_runs

    result = await pipeline.run()
    assert result is None

    rows = pipeline.runs_store.load()
    assert len(rows) == 1
    rec = rows[0]
    assert rec.input_msg_count == 0
    assert rec.skip_reason == SKIP_NO_MESSAGES
    assert rec.error is None
    assert rec.ended_at is not None
    assert rec.status == "skip"


@pytest.mark.asyncio
async def test_successful_cycle_records_metrics(pipeline_with_runs):
    """정상 종료 시 input_msg_count, generated_insight_count 가 모두 채워져야 한다."""
    store, pipeline, _, _, _ = pipeline_with_runs

    mock_response = MagicMock()
    mock_response.text = (
        '{"memory": "## d\\n- x", "user_insights": "- 정치 뉴스 관심", '
        '"user_insights_meta": [{"topic": "정치뉴스", "text": "정치 뉴스 관심"}], '
        '"soul_updates": "", "agent_updates": ""}'
    )
    mock_router = MagicMock()
    mock_router.send = AsyncMock(return_value=mock_response)
    pipeline._router = mock_router

    store.add_message(ConversationMessage(
        role=MessageRole.USER, content="정치 뉴스 알려줘"
    ))
    store.add_message(ConversationMessage(
        role=MessageRole.ASSISTANT, content="오늘의 헤드라인입니다"
    ))

    result = await pipeline.run()
    assert result is not None

    rows = pipeline.runs_store.load()
    assert len(rows) == 1
    rec = rows[0]
    assert rec.status == "success"
    assert rec.input_msg_count == 2
    # 1개 신규 인사이트가 generated 됨.
    assert rec.generated_insight_count == 1
    assert rec.rejected_count == 0
    assert rec.error is None
    assert rec.skip_reason is None
    assert rec.ended_at is not None
    assert rec.duration_seconds is not None and rec.duration_seconds >= 0


@pytest.mark.asyncio
async def test_preflight_failure_records_skip_reason(tmp_path):
    """USER.md 에 managed 마커가 없으면 preflight 실패 → skip_reason='preflight_failed'."""
    db = tmp_path / "test.db"
    store = ConversationStore(db)

    memory_file = tmp_path / "MEMORY.md"
    memory_file.write_text(
        "<!-- managed:dreaming:journal -->\n<!-- /managed:dreaming:journal -->\n"
    )
    # USER.md 에 마커 누락 — preflight 가 ProtectedSectionError 를 던진다.
    user_file = tmp_path / "USER.md"
    user_file.write_text("# 마커 없음\n")
    runs_file = tmp_path / "runs.jsonl"

    pipeline = DreamingPipeline(
        store, memory_file, user_file=user_file, runs_file=runs_file,
    )
    store.add_message(ConversationMessage(
        role=MessageRole.USER, content="hello"
    ))

    result = await pipeline.run()
    assert result is None

    rows = pipeline.runs_store.load()
    assert len(rows) == 1
    rec = rows[0]
    assert rec.skip_reason == SKIP_PREFLIGHT_FAILED
    assert rec.input_msg_count == 1
    # 진단 메시지(어떤 섹션이 누락이었는지)가 details 에 들어 있어야 한다.
    assert rec.details.get("message")
    assert rec.error is None


@pytest.mark.asyncio
async def test_empty_results_records_skip_reason(pipeline_with_runs):
    """LLM 이 모든 산출물을 빈 값으로 반환하면 skip_reason='empty_results'."""
    store, pipeline, _, _, _ = pipeline_with_runs

    mock_response = MagicMock()
    mock_response.text = (
        '{"memory": "", "user_insights": "", "user_insights_meta": [], '
        '"soul_updates": "", "agent_updates": ""}'
    )
    mock_router = MagicMock()
    mock_router.send = AsyncMock(return_value=mock_response)
    pipeline._router = mock_router

    store.add_message(ConversationMessage(
        role=MessageRole.USER, content="?"
    ))

    result = await pipeline.run()
    assert result is None

    rows = pipeline.runs_store.load()
    assert len(rows) == 1
    rec = rows[0]
    assert rec.skip_reason == SKIP_EMPTY_RESULTS
    assert rec.input_msg_count == 1
    assert rec.generated_insight_count == 0
    assert rec.error is None


@pytest.mark.asyncio
async def test_unexpected_exception_records_error(pipeline_with_runs):
    """LLM 호출에서 예외가 던져지면 error 필드에 메시지가 기록되어야 한다."""
    store, pipeline, _, _, _ = pipeline_with_runs

    mock_router = MagicMock()
    mock_router.send = AsyncMock(side_effect=RuntimeError("router exploded"))
    pipeline._router = mock_router

    store.add_message(ConversationMessage(
        role=MessageRole.USER, content="trigger"
    ))

    # summarize 안에서 fallback 으로 처리될 가능성 — 직접 _run_after_preflight 을 폭파.
    # 테스트 의도: 사이클 본문에서 발생한 예외가 메트릭 행에 error 로 잡힌다.
    original = pipeline._run_after_preflight

    async def boom(**kwargs):
        raise RuntimeError("simulated mid-cycle failure")

    pipeline._run_after_preflight = boom

    result = await pipeline.run()
    assert result is None

    rows = pipeline.runs_store.load()
    assert len(rows) == 1
    rec = rows[0]
    assert rec.error is not None
    assert "simulated mid-cycle failure" in rec.error
    assert rec.skip_reason is None
    assert rec.status == "error"

    # 정리: 다른 테스트에 영향 없도록 원복.
    pipeline._run_after_preflight = original


@pytest.mark.asyncio
async def test_rejected_count_tracks_blocklisted_topics(tmp_path):
    """차단 리스트에 등록된 topic 이 추출되면 rejected_count 가 증가해야 한다."""
    db = tmp_path / "test.db"
    store = ConversationStore(db)
    memory_file = tmp_path / "MEMORY.md"
    memory_file.write_text(
        "<!-- managed:dreaming:journal -->\n<!-- /managed:dreaming:journal -->\n"
    )
    user_file = tmp_path / "USER.md"
    user_file.write_text(
        "# User\n<!-- managed:dreaming:insights -->\n<!-- /managed:dreaming:insights -->\n"
    )
    runs_file = tmp_path / "runs.jsonl"

    pipeline = DreamingPipeline(
        store, memory_file, user_file=user_file, runs_file=runs_file,
    )
    # reject blocklist 에 'banned' topic 등록 — 다음 사이클에서 추출 시 drop 되어야 한다.
    pipeline.reject_blocklist.add("banned")

    mock_response = MagicMock()
    mock_response.text = (
        '{"memory": "## d\\n- x", "user_insights": "- ok 인사이트", '
        '"user_insights_meta": [{"topic": "banned", "text": "차단 대상"},'
        ' {"topic": "허용", "text": "통과"}], '
        '"soul_updates": "", "agent_updates": ""}'
    )
    mock_router = MagicMock()
    mock_router.send = AsyncMock(return_value=mock_response)
    pipeline._router = mock_router

    store.add_message(ConversationMessage(
        role=MessageRole.USER, content="hi"
    ))

    result = await pipeline.run()
    assert result is not None

    rows = pipeline.runs_store.load()
    assert len(rows) == 1
    rec = rows[0]
    assert rec.rejected_count == 1
    # 통과한 1개만 generated 로 카운트.
    assert rec.generated_insight_count == 1


@pytest.mark.asyncio
async def test_metrics_disabled_when_no_runs_file(tmp_path):
    """runs_file 미주입 시 사이클 동작은 그대로, 메트릭 기록만 비활성."""
    db = tmp_path / "test.db"
    store = ConversationStore(db)
    memory_file = tmp_path / "MEMORY.md"
    memory_file.write_text(
        "<!-- managed:dreaming:journal -->\n<!-- /managed:dreaming:journal -->\n"
    )
    pipeline = DreamingPipeline(store, memory_file)
    # runs_store 가 None 이어야 한다.
    assert pipeline.runs_store is None

    # 사이클 동작은 정상 — 메시지 0 건이라 None 반환.
    result = await pipeline.run()
    assert result is None
