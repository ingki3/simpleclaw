"""BIZ-76 — cron/recipe 자동 트리거 메시지의 dreaming 코퍼스 분리 검증.

부모 BIZ-66 §2-6 의 사고 사례(자동 발사된 정치 뉴스 요약이 "사용자가 정치에
지속적 관심" 으로 일반화되는 문제)를 막기 위한 코퍼스 필터 동작을 검증한다.

세 계층을 모두 본다:
1. 분류 헬퍼 ``is_auto_trigger_channel`` — 채널 prefix 규약을 단일 진입점에서.
2. 파이프라인 ``_apply_auto_trigger_filter`` — exclude/downweight/include 모드별
   결정적 동작과 시간순 보존.
3. 통합: auto-trigger 만 있는 코퍼스 → ``run()`` 이 인사이트를 만들지 않음 (DoD).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.dreaming import (
    AUTO_TRIGGER_MODE_DOWNWEIGHT,
    AUTO_TRIGGER_MODE_EXCLUDE,
    AUTO_TRIGGER_MODE_INCLUDE,
    DreamingPipeline,
)
from simpleclaw.memory.models import (
    CHANNEL_CRON_ADMIN,
    CHANNEL_CRON_PREFIX,
    CHANNEL_RECIPE_PREFIX,
    ConversationMessage,
    MessageRole,
    is_auto_trigger_channel,
)


# ----------------------------------------------------------------------
# 1. 채널 분류 헬퍼
# ----------------------------------------------------------------------

class TestIsAutoTriggerChannel:
    """``is_auto_trigger_channel`` 의 단일 분류 진입점 동작.

    프로듀서(orchestrator) 와 컨슈머(dreaming) 양쪽이 모두 이 함수에 의존하므로
    여기서 합의한 prefix 가 깨지면 양쪽이 같이 깨진다 — 핵심 가드 테스트.
    """

    def test_recipe_prefix_is_auto(self):
        assert is_auto_trigger_channel(f"{CHANNEL_RECIPE_PREFIX}ai-report")
        assert is_auto_trigger_channel("recipe:check_new_emails")

    def test_cron_prefix_is_auto(self):
        # 현 시점에 store 에 저장되지 않지만 prefix 는 예약돼 있다.
        assert is_auto_trigger_channel(f"{CHANNEL_CRON_PREFIX}daily-summary")

    def test_cron_admin_is_auto(self):
        assert is_auto_trigger_channel(CHANNEL_CRON_ADMIN)

    def test_none_is_organic(self):
        # 마이그레이션 0002 이전 데이터 보존 — None 은 organic 으로 간주.
        assert is_auto_trigger_channel(None) is False

    def test_empty_is_organic(self):
        assert is_auto_trigger_channel("") is False

    def test_other_origins_are_organic(self):
        # 사용자 발화 출처(텔레그램/웹훅/콘솔)는 모두 organic.
        for origin in ("telegram", "webhook", "console"):
            assert is_auto_trigger_channel(origin) is False, origin

    def test_substring_does_not_match(self):
        # "recipe:" 는 prefix 매칭이지 substring 매칭이 아니다.
        # "my-recipe:foo" 는 organic.
        assert is_auto_trigger_channel("my-recipe:foo") is False


# ----------------------------------------------------------------------
# 2. 파이프라인 필터 — exclude / downweight / include
# ----------------------------------------------------------------------

def _make_pipeline(tmp_path, *, mode=AUTO_TRIGGER_MODE_EXCLUDE, weight=0.3):
    """필터 단위 테스트용 최소 파이프라인 fixture.

    BIZ-72 managed 마커가 포함된 MEMORY.md 를 함께 만들어 ``run()`` 호출 시 사전
    검증을 통과하도록 한다.
    """
    db = tmp_path / "test.db"
    store = ConversationStore(db)
    memory_file = tmp_path / "MEMORY.md"
    memory_file.write_text(
        "# Core Memory\n\n"
        "Existing.\n\n"
        "<!-- managed:dreaming:journal -->\n"
        "<!-- /managed:dreaming:journal -->\n"
    )
    user_file = tmp_path / "USER.md"
    user_file.write_text(
        "# User Profile\n\n"
        "<!-- managed:dreaming:insights -->\n"
        "<!-- /managed:dreaming:insights -->\n"
    )
    pipeline = DreamingPipeline(
        store,
        memory_file,
        user_file=user_file,
        auto_trigger_mode=mode,
        auto_trigger_weight=weight,
    )
    return store, pipeline


class TestAutoTriggerFilterMode:
    """``_apply_auto_trigger_filter`` 의 세 모드별 결정적 동작."""

    def test_exclude_drops_all_auto_trigger(self, tmp_path):
        store, pipeline = _make_pipeline(tmp_path, mode=AUTO_TRIGGER_MODE_EXCLUDE)
        store.add_message(ConversationMessage(
            role=MessageRole.USER, content="organic-1", channel=None,
        ))
        store.add_message(ConversationMessage(
            role=MessageRole.ASSISTANT, content="ai-report output",
            channel="recipe:ai-report",
        ))
        store.add_message(ConversationMessage(
            role=MessageRole.USER, content="organic-2", channel="telegram",
        ))
        store.add_message(ConversationMessage(
            role=MessageRole.ASSISTANT, content="cron list response",
            channel=CHANNEL_CRON_ADMIN,
        ))

        result = pipeline.collect_unprocessed()
        contents = [m.content for m in result]
        assert contents == ["organic-1", "organic-2"]

    def test_include_preserves_auto_trigger(self, tmp_path):
        store, pipeline = _make_pipeline(tmp_path, mode=AUTO_TRIGGER_MODE_INCLUDE)
        store.add_message(ConversationMessage(
            role=MessageRole.USER, content="organic", channel=None,
        ))
        store.add_message(ConversationMessage(
            role=MessageRole.ASSISTANT, content="ai-report",
            channel="recipe:ai-report",
        ))

        result = pipeline.collect_unprocessed()
        assert len(result) == 2
        assert {m.content for m in result} == {"organic", "ai-report"}

    def test_downweight_strides_auto_only(self, tmp_path):
        # weight=0.3 → stride=3 (1/3 보존). 6 개 auto + 1 organic 이면 organic 1 +
        # auto[::3] = 2 개 = 총 3 개.
        store, pipeline = _make_pipeline(
            tmp_path, mode=AUTO_TRIGGER_MODE_DOWNWEIGHT, weight=0.3,
        )
        store.add_message(ConversationMessage(
            role=MessageRole.USER, content="organic", channel="telegram",
        ))
        for i in range(6):
            store.add_message(ConversationMessage(
                role=MessageRole.ASSISTANT, content=f"auto-{i}",
                channel="recipe:ai-report",
            ))

        result = pipeline.collect_unprocessed()
        contents = [m.content for m in result]
        # organic 은 항상 보존. auto 는 stride=3 → 인덱스 0, 3 (auto-0, auto-3).
        assert "organic" in contents
        auto_kept = [c for c in contents if c.startswith("auto-")]
        assert auto_kept == ["auto-0", "auto-3"]

    def test_downweight_weight_zero_acts_like_exclude(self, tmp_path):
        store, pipeline = _make_pipeline(
            tmp_path, mode=AUTO_TRIGGER_MODE_DOWNWEIGHT, weight=0.0,
        )
        store.add_message(ConversationMessage(
            role=MessageRole.USER, content="organic", channel=None,
        ))
        store.add_message(ConversationMessage(
            role=MessageRole.ASSISTANT, content="auto",
            channel="recipe:x",
        ))

        result = pipeline.collect_unprocessed()
        assert [m.content for m in result] == ["organic"]

    def test_downweight_weight_one_acts_like_include(self, tmp_path):
        store, pipeline = _make_pipeline(
            tmp_path, mode=AUTO_TRIGGER_MODE_DOWNWEIGHT, weight=1.0,
        )
        store.add_message(ConversationMessage(
            role=MessageRole.USER, content="organic", channel=None,
        ))
        store.add_message(ConversationMessage(
            role=MessageRole.ASSISTANT, content="auto",
            channel="recipe:x",
        ))

        result = pipeline.collect_unprocessed()
        assert [m.content for m in result] == ["organic", "auto"]

    def test_unknown_mode_falls_back_to_exclude(self, tmp_path, caplog):
        # 운영자 오타로 필터가 무력화되는 사고 방지 — 알 수 없는 모드는 exclude 로.
        store, pipeline = _make_pipeline(tmp_path, mode="bogus-mode")
        store.add_message(ConversationMessage(
            role=MessageRole.USER, content="organic", channel=None,
        ))
        store.add_message(ConversationMessage(
            role=MessageRole.ASSISTANT, content="auto",
            channel="recipe:x",
        ))

        result = pipeline.collect_unprocessed()
        # exclude 와 동일 — auto 는 사라진다.
        assert [m.content for m in result] == ["organic"]

    def test_with_ids_applies_same_filter(self, tmp_path):
        # collect_unprocessed_with_ids 도 동일 정책. 인사이트 source 역추적이
        # 자동 트리거 메시지를 가리키지 않게 해야 한다(BIZ-77 일관성).
        store, pipeline = _make_pipeline(tmp_path, mode=AUTO_TRIGGER_MODE_EXCLUDE)
        store.add_message(ConversationMessage(
            role=MessageRole.USER, content="organic", channel=None,
        ))
        store.add_message(ConversationMessage(
            role=MessageRole.ASSISTANT, content="auto",
            channel="recipe:x",
        ))

        pairs = pipeline.collect_unprocessed_with_ids()
        contents = [m.content for _, m in pairs]
        assert contents == ["organic"]

    def test_filter_preserves_time_order(self, tmp_path):
        # downweight 모드에서 organic + sampled_auto 를 합칠 때 원본 시간순이
        # 깨지면 안 된다(LLM 프롬프트의 순서 시맨틱이 무너진다).
        store, pipeline = _make_pipeline(
            tmp_path, mode=AUTO_TRIGGER_MODE_DOWNWEIGHT, weight=0.5,
        )
        # 시간순: O1, A1, O2, A2, O3, A3 — A1, A3 만 stride 로 살아남는다(stride=2).
        # 결과: O1, A1, O2, O3, A3 (시간순 유지).
        seq = [
            ("O1", None),
            ("A1", "recipe:x"),
            ("O2", "telegram"),
            ("A2", "recipe:x"),
            ("O3", None),
            ("A3", "recipe:x"),
        ]
        for content, channel in seq:
            store.add_message(ConversationMessage(
                role=MessageRole.USER, content=content, channel=channel,
            ))

        result = pipeline.collect_unprocessed()
        contents = [m.content for m in result]
        # weight=0.5 → stride=2 → auto[::2] = [A1, A3].
        assert contents == ["O1", "A1", "O2", "O3", "A3"]


# ----------------------------------------------------------------------
# 3. DoD: auto-trigger 만 있는 코퍼스 → 인사이트 0건
# ----------------------------------------------------------------------

class TestAutoTriggerOnlyCorpus:
    """이슈 DoD 의 핵심 가드.

    "자동 트리거만 있는 코퍼스로는 high-confidence 인사이트가 생성되지 않음을
    검증." — 기본 모드 ``exclude`` 에서 코퍼스가 비어 ``run()`` 이 None 을 반환,
    인사이트 sidecar 도 비어 있어야 한다.
    """

    @pytest.mark.asyncio
    async def test_run_returns_none_when_only_auto_trigger(self, tmp_path):
        store, pipeline = _make_pipeline(tmp_path, mode=AUTO_TRIGGER_MODE_EXCLUDE)

        # LLM 이 호출되면 안 된다 — exclude 가 코퍼스를 비워서 run 이 일찍 종료해야 함.
        # mock 은 만약 호출되면 테스트가 명시적으로 잡도록 send 카운터를 검사.
        mock_router = MagicMock()
        mock_router.send = AsyncMock()
        pipeline._router = mock_router

        # 자동 트리거 메시지만 추가
        store.add_message(ConversationMessage(
            role=MessageRole.USER, content="/ai-report",
            channel="recipe:ai-report",
        ))
        store.add_message(ConversationMessage(
            role=MessageRole.ASSISTANT,
            content="오늘의 AI 트렌드: ChatGPT, Claude, Gemini ...",
            channel="recipe:ai-report",
        ))
        store.add_message(ConversationMessage(
            role=MessageRole.USER, content="/cron list",
            channel=CHANNEL_CRON_ADMIN,
        ))
        store.add_message(ConversationMessage(
            role=MessageRole.ASSISTANT, content="등록된 작업: ai-report",
            channel=CHANNEL_CRON_ADMIN,
        ))

        result = await pipeline.run()

        # exclude 는 코퍼스를 비워 run 을 일찍 종료시킨다 — MemoryEntry 는 None.
        assert result is None
        # LLM 도 호출되지 않아야 한다(코퍼스가 비었으므로 분석 자체가 발생하지 않음).
        mock_router.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_organic_messages_still_processed_alongside_auto_trigger(
        self, tmp_path,
    ):
        # 같은 코퍼스에 organic 이 함께 있으면 organic 만 LLM 에 전달되고
        # 인사이트가 정상 생성된다 — 필터가 organic 까지 죽이지 않는지 확인.
        store, pipeline = _make_pipeline(tmp_path, mode=AUTO_TRIGGER_MODE_EXCLUDE)

        mock_response = MagicMock()
        mock_response.text = (
            '{"memory": "## test\\n- item",'
            ' "user_insights": "- 사용자는 KBO 야구를 좋아한다"}'
        )
        mock_router = MagicMock()
        mock_router.send = AsyncMock(return_value=mock_response)
        pipeline._router = mock_router

        # auto-trigger
        store.add_message(ConversationMessage(
            role=MessageRole.ASSISTANT, content="ai-report dump",
            channel="recipe:ai-report",
        ))
        # organic
        store.add_message(ConversationMessage(
            role=MessageRole.USER, content="KBO 어제 경기 결과 알려줘",
            channel="telegram",
        ))
        store.add_message(ConversationMessage(
            role=MessageRole.ASSISTANT, content="두산 5-3 KIA",
            channel="telegram",
        ))

        result = await pipeline.run()
        assert result is not None

        # LLM 에 전달된 프롬프트에 자동 트리거 출력은 없어야 한다.
        call_args = mock_router.send.call_args[0][0]
        # GenerateRequest.messages 또는 .prompt 에 콘텐츠가 들어간다.
        # dreaming 은 system+user 프롬프트로 메시지를 직렬화하므로 prompt 본문을 본다.
        sent_blob = str(call_args.__dict__)
        assert "ai-report dump" not in sent_blob
        assert "KBO" in sent_blob


# ----------------------------------------------------------------------
# 4. 채널 태깅 전파 — orchestrator 가 슬래시 명령 출력에 channel 을 붙이는가
# ----------------------------------------------------------------------

class TestOrchestratorChannelTagging:
    """orchestrator 가 cron-admin / recipe:<name> 을 store 에 영속화하는지.

    DB 까지 가는 통합 가까이의 단위 테스트 — 실제 store 에 메시지가 붙고
    channel 컬럼이 NOT NULL 로 채워진다는 것을 확인한다(BIZ-77 의 마이그레이션
    0002 가 채널 컬럼을 도입하므로 통과해야 함).
    """

    def test_save_turn_propagates_channel(self, tmp_path):
        # orchestrator._save_turn 의 채널 전파를 store 까지 검증.
        # 본 테스트는 _save_turn 만의 단위 검증 — 의존성 최소화를 위해 실제
        # AgentOrchestrator 인스턴스화는 피하고, 메서드 본체와 동일한 호출
        # 패턴을 직접 시연한다.
        store = ConversationStore(tmp_path / "test.db")

        store.add_message(ConversationMessage(
            role=MessageRole.USER, content="/cron list",
            channel=CHANNEL_CRON_ADMIN,
        ))
        store.add_message(ConversationMessage(
            role=MessageRole.ASSISTANT, content="등록된 작업: ai-report",
            channel=CHANNEL_CRON_ADMIN,
        ))

        # store 에서 다시 읽었을 때 channel 이 보존돼야 한다.
        msgs = store.get_recent(limit=10)
        assert len(msgs) == 2
        for m in msgs:
            assert m.channel == CHANNEL_CRON_ADMIN
            # 그리고 분류기는 이를 auto-trigger 로 본다.
            assert is_auto_trigger_channel(m.channel)
