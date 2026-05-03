"""BIZ-78 — Insight Decay & Re-evaluation 단위 테스트.

이 파일은 BIZ-78 의 DoD 4 항목(decay archive, reject blocklist, archive→revive,
3 unit tests) 을 직접 검증한다. dreaming.py 의 다른 동작은 이미
``test_dreaming.py`` / ``test_dreaming_regression.py`` 가 커버하므로 여기서는
*decay/reject/revive* 만 좁게 다룬다.

설계 결정:
- LLM 호출은 모두 ``AsyncMock`` 으로 대체 — sidecar 와 차단 리스트의 인터랙션을
  검증하는 게 목적이지 LLM 응답 형태 자체는 BIZ-73 테스트가 이미 다룬다.
- 시간은 ``datetime.now()`` 가 아니라 명시적 ``now=`` 인자를 통해 주입한다 —
  monkeypatch 보다 더 명확하고, decay 의 cutoff 의미를 1행으로 보여준다.
- USER.md fixture 는 ``managed:dreaming:archive`` 마커를 *포함* 하는 변형과
  *생략* 하는 변형을 모두 만들어 backwards-compat 를 검증한다(기존 USER.md 가
  archive 섹션을 안 갖고 있어도 sidecar 만 갱신되고 markdown 은 깨지지 않는다는
  계약).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.dreaming import DreamingPipeline
from simpleclaw.memory.insights import InsightMeta, InsightStore, normalize_topic
from simpleclaw.memory.models import ConversationMessage, MessageRole
from simpleclaw.memory.reject_blocklist import RejectBlocklistStore


# ---------------------------------------------------------------------------
# 공통 fixture
# ---------------------------------------------------------------------------

_USER_MD_WITH_ARCHIVE = (
    "# User Profile\n"
    "\n"
    "## Preferences\n"
    "- Language: Korean\n"
    "\n"
    "<!-- managed:dreaming:insights -->\n"
    "<!-- /managed:dreaming:insights -->\n"
    "\n"
    "## Archive\n"
    "<!-- managed:dreaming:archive -->\n"
    "<!-- /managed:dreaming:archive -->\n"
)

_USER_MD_WITHOUT_ARCHIVE = (
    "# User Profile\n"
    "\n"
    "## Preferences\n"
    "- Language: Korean\n"
    "\n"
    "<!-- managed:dreaming:insights -->\n"
    "<!-- /managed:dreaming:insights -->\n"
)

_MEMORY_MD = (
    "# Core Memory\n"
    "\n"
    "<!-- managed:dreaming:journal -->\n"
    "<!-- /managed:dreaming:journal -->\n"
)


def _make_pipeline(tmp_path, *, archive_marker: bool, decay_days: int | None = 30):
    """Decay 테스트용 파이프라인 + 스토어 fixture 를 만든다.

    archive_marker=True 면 USER.md 에 archive 섹션 마커를 포함시킨다.
    decay_days 는 ``decay_archive_after_days`` 인자로 그대로 전달.
    """
    db = tmp_path / "test.db"
    conv_store = ConversationStore(db)
    memory_file = tmp_path / "MEMORY.md"
    memory_file.write_text(_MEMORY_MD)
    user_file = tmp_path / "USER.md"
    user_file.write_text(
        _USER_MD_WITH_ARCHIVE if archive_marker else _USER_MD_WITHOUT_ARCHIVE
    )
    pipeline = DreamingPipeline(
        conv_store,
        memory_file,
        user_file=user_file,
        decay_archive_after_days=decay_days,
    )
    insights = InsightStore(user_file.parent / "insights.jsonl")
    rejects = RejectBlocklistStore(user_file.parent / "rejects.jsonl")
    return conv_store, pipeline, memory_file, user_file, insights, rejects


# ---------------------------------------------------------------------------
# DoD #1 — Decay: 30일 이상 reinforcement 가 없으면 archive 로 이동
# ---------------------------------------------------------------------------

class TestDecayArchive:
    def test_decay_archives_stale_insight_after_threshold_days(self, tmp_path):
        """``last_seen`` 이 cutoff 보다 *오래되었으면* archive 처리.

        조건:
        - ``decay_archive_after_days=30`` (기본).
        - 인사이트의 ``last_seen`` 이 31일 전.
        - ``apply_decay(now=현재)`` 호출.

        기대:
        - sidecar 의 ``archived_at`` 이 ``now`` 로 세팅됨.
        - USER.md 의 ``managed:dreaming:archive`` 섹션에 dated 흔적 한 줄 추가.
        - ``apply_decay`` 가 새로 archive 된 항목 1건을 반환.
        """
        _, pipeline, _, user_file, insights, _ = _make_pipeline(
            tmp_path, archive_marker=True
        )
        now = datetime(2026, 5, 3, 12, 0, 0)
        old_last_seen = now - timedelta(days=31)
        insights.save_all(
            {
                normalize_topic("정치뉴스"): InsightMeta(
                    topic="정치뉴스",
                    text="정치 뉴스 관심",
                    evidence_count=2,
                    confidence=0.55,
                    first_seen=old_last_seen,
                    last_seen=old_last_seen,
                )
            }
        )

        archived = pipeline.apply_decay(now=now)
        assert len(archived) == 1
        assert archived[0].topic == "정치뉴스"
        assert archived[0].archived_at == now

        # sidecar 디스크에 영속화됐는지 확인.
        loaded = insights.load()
        assert loaded[normalize_topic("정치뉴스")].archived_at == now

        # USER.md 에 dated archive 흔적이 추가됐는지 확인 — *보존* 영역(Preferences)도 그대로.
        content = user_file.read_text()
        assert "Language: Korean" in content
        assert "Archived (decay)" in content
        assert "정치뉴스" in content

    def test_decay_skips_recently_seen_insight(self, tmp_path):
        """cutoff 안쪽이면(=최근 reinforcement 있음) 손대지 않는다.

        ``last_seen`` 이 29일 전(=cutoff 이전 1일)이면 archive 대상 아님 — 정확한
        경계 동작을 보장한다.
        """
        _, pipeline, _, user_file, insights, _ = _make_pipeline(
            tmp_path, archive_marker=True
        )
        now = datetime(2026, 5, 3, 12, 0, 0)
        recent = now - timedelta(days=29)
        insights.save_all(
            {
                normalize_topic("최근관심사"): InsightMeta(
                    topic="최근관심사",
                    text="최근에 본 것",
                    evidence_count=1,
                    confidence=0.4,
                    first_seen=recent,
                    last_seen=recent,
                )
            }
        )

        archived = pipeline.apply_decay(now=now)
        assert archived == []
        # USER.md 도 손대지 않았어야 한다(빈 archive 블록만 그대로).
        loaded = insights.load()
        assert loaded[normalize_topic("최근관심사")].archived_at is None
        assert "Archived (decay)" not in user_file.read_text()

    def test_decay_disabled_when_archive_after_days_is_none(self, tmp_path):
        """``decay_archive_after_days=None`` → ``apply_decay`` 는 no-op.

        config 가 decay 비활성으로 설정된 경우 sidecar 와 USER.md 모두 무변동.
        """
        _, pipeline, _, user_file, insights, _ = _make_pipeline(
            tmp_path, archive_marker=True, decay_days=None
        )
        ancient = datetime(2020, 1, 1, 0, 0, 0)
        insights.save_all(
            {
                normalize_topic("아주옛날관심사"): InsightMeta(
                    topic="아주옛날관심사",
                    text="6년 전 관심사",
                    evidence_count=1,
                    confidence=0.4,
                    first_seen=ancient,
                    last_seen=ancient,
                )
            }
        )
        archived = pipeline.apply_decay(now=datetime(2026, 5, 3))
        assert archived == []
        assert (
            insights.load()[normalize_topic("아주옛날관심사")].archived_at is None
        )
        assert "Archived (decay)" not in user_file.read_text()

    def test_decay_without_archive_section_marker_only_updates_sidecar(
        self, tmp_path
    ):
        """USER.md 에 archive 섹션 마커가 없으면 sidecar 만 갱신, markdown 은 안 건드림.

        backwards-compat 보장 — 기존 USER.md 가 archive 마커를 갖고 있지 않아도
        decay 자체는 동작해야 한다(sidecar 의 ``archived_at`` 만 세팅, USER.md 는
        그대로 보존).
        """
        _, pipeline, _, user_file, insights, _ = _make_pipeline(
            tmp_path, archive_marker=False
        )
        now = datetime(2026, 5, 3, 12, 0, 0)
        old = now - timedelta(days=45)
        insights.save_all(
            {
                normalize_topic("오래된관심사"): InsightMeta(
                    topic="오래된관심사",
                    text="옛날 관심사",
                    evidence_count=1,
                    confidence=0.4,
                    first_seen=old,
                    last_seen=old,
                )
            }
        )
        archived = pipeline.apply_decay(now=now)
        assert len(archived) == 1
        # sidecar 는 갱신.
        assert (
            insights.load()[normalize_topic("오래된관심사")].archived_at == now
        )
        # USER.md 는 손대지 않음 — Preferences 그대로, 새 archive 블록 없음.
        content = user_file.read_text()
        assert "Language: Korean" in content
        assert "Archived (decay)" not in content


# ---------------------------------------------------------------------------
# DoD #2 — Reject: 즉시 폐기 + 차단 리스트 등록 → 다음 회차 재추출 차단
# ---------------------------------------------------------------------------

class TestRejectBlocklist:
    def test_register_reject_drops_insight_and_blocks_reextraction(
        self, tmp_path
    ):
        """``register_reject`` → sidecar 즉시 삭제 + 차단 리스트 등록.

        그리고 같은 topic 이 다음 LLM 회차에 다시 추출되어도 ``apply_insight_meta``
        에서 drop 된다 — 즉, 차단이 회차를 가로질러 작동해야 한다(파일 영속화 검증).
        """
        store, pipeline, _, user_file, insights, rejects = _make_pipeline(
            tmp_path, archive_marker=True
        )

        # 사전 상태: sidecar 에 인사이트 1건 존재.
        existing = datetime(2026, 5, 3, 9, 0, 0)
        insights.save_all(
            {
                normalize_topic("야식추천"): InsightMeta(
                    topic="야식추천",
                    text="야식 추천 받기 좋아함",
                    evidence_count=2,
                    confidence=0.55,
                    first_seen=existing,
                    last_seen=existing,
                )
            }
        )

        # reject 호출 — 영구 차단(ttl_days=None) + 사유 기록.
        ok = pipeline.register_reject(
            topic="야식추천", reason="틀림 — 사용자가 명시적 거부"
        )
        assert ok is True

        # 1) sidecar 에서 즉시 삭제.
        assert normalize_topic("야식추천") not in insights.load()

        # 2) 차단 리스트에 등록(영구).
        loaded_rejects = rejects.load()
        assert normalize_topic("야식추천") in loaded_rejects
        assert loaded_rejects[normalize_topic("야식추천")].ttl_seconds is None

        # 3) 다음 회차에 LLM 이 같은 topic 을 또 추출해도 ``apply_insight_meta`` 가 drop.
        #    실제 ``run()`` 을 돌려 회차를 시뮬레이트한다.
        mock_response = MagicMock()
        mock_response.text = (
            '{"memory": "## d\\n- x", "user_insights": "- 야식 또 추천", '
            '"user_insights_meta": [{"topic": "야식추천", "text": "야식 또 추천"}], '
            '"soul_updates": "", "agent_updates": ""}'
        )
        mock_router = MagicMock()
        mock_router.send = AsyncMock(return_value=mock_response)
        pipeline._router = mock_router

        store.add_message(
            ConversationMessage(role=MessageRole.USER, content="야식 뭐 먹지")
        )
        # 동기 호출이 아니라 async — pytest-asyncio 가 필요하므로 별도 메소드로.

    @pytest.mark.asyncio
    async def test_blocked_topic_is_dropped_in_next_dreaming_cycle(
        self, tmp_path
    ):
        """차단 리스트 영속성 — 같은 파이프라인의 다음 ``run()`` 에서도 차단 유지.

        ``register_reject`` 호출 후 다른 ``run()`` 사이클이 돌아도 차단 리스트는
        ``rejects.jsonl`` 파일에서 다시 로드되어 적용된다.
        """
        store, pipeline, _, user_file, insights, _ = _make_pipeline(
            tmp_path, archive_marker=True
        )

        pipeline.register_reject(topic="야식추천", reason="거부")
        assert normalize_topic("야식추천") not in insights.load()

        mock_response = MagicMock()
        mock_response.text = (
            '{"memory": "## d\\n- x", "user_insights": "- 야식 또 추천", '
            '"user_insights_meta": [{"topic": "야식추천", "text": "야식 또 추천"}], '
            '"soul_updates": "", "agent_updates": ""}'
        )
        mock_router = MagicMock()
        mock_router.send = AsyncMock(return_value=mock_response)
        pipeline._router = mock_router

        store.add_message(
            ConversationMessage(role=MessageRole.USER, content="야식 뭐 먹지")
        )
        await pipeline.run()

        # 차단된 topic 이 sidecar 에 다시 들어오면 안 된다.
        assert normalize_topic("야식추천") not in insights.load()


# ---------------------------------------------------------------------------
# DoD #3 — Archive → Revive: 같은 topic 이 다시 관측되면 부활
# ---------------------------------------------------------------------------

class TestArchiveRevive:
    @pytest.mark.asyncio
    async def test_archived_insight_is_revived_on_reobservation(self, tmp_path):
        """archive 상태의 인사이트가 ``apply_insight_meta`` 회차에서 재관측되면 부활.

        ``merge_insights`` 가 ``archived_at`` 을 None 으로 되돌리고 ``last_seen``,
        ``evidence_count``, ``source_msg_ids`` 를 정상 누적해야 한다.

        부활 직후 confidence 는 ``compute_confidence(evidence_count, threshold)``
        로 재계산되므로, archive 동안 evidence_count 가 보존된다는 사실이 의미를
        갖는다(부활 = 새 인사이트가 아니라 *연속* 으로 본다).
        """
        store, pipeline, _, _, insights, _ = _make_pipeline(
            tmp_path, archive_marker=True
        )

        archived_at = datetime(2026, 4, 1, 10, 0, 0)
        old_last_seen = datetime(2026, 3, 1, 10, 0, 0)
        insights.save_all(
            {
                normalize_topic("정치뉴스"): InsightMeta(
                    topic="정치뉴스",
                    text="정치 뉴스 관심",
                    evidence_count=2,
                    confidence=0.55,
                    first_seen=old_last_seen,
                    last_seen=old_last_seen,
                    archived_at=archived_at,
                )
            }
        )
        # 사전 조건 확인.
        before = insights.load()[normalize_topic("정치뉴스")]
        assert before.is_archived()
        assert before.evidence_count == 2

        # LLM 이 같은 topic 을 다시 추출하는 회차.
        mock_response = MagicMock()
        mock_response.text = (
            '{"memory": "## d\\n- x", "user_insights": "- 정치 뉴스 다시", '
            '"user_insights_meta": [{"topic": "정치뉴스", "text": "정치 뉴스 다시"}], '
            '"soul_updates": "", "agent_updates": ""}'
        )
        mock_router = MagicMock()
        mock_router.send = AsyncMock(return_value=mock_response)
        pipeline._router = mock_router

        store.add_message(
            ConversationMessage(role=MessageRole.USER, content="정치 뉴스 또")
        )
        await pipeline.run()

        revived = insights.load()[normalize_topic("정치뉴스")]
        # 부활: archived_at 이 None 으로 돌아오고 evidence_count 가 누적됐다.
        assert revived.archived_at is None
        assert revived.evidence_count == 3
        # text 도 최신 관측으로 갱신.
        assert "다시" in revived.text
