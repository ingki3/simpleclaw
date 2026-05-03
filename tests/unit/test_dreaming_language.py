"""BIZ-80 — Dreaming 1차 언어 정규화 단위 테스트.

DoD 핵심 검증:

1. ``language_policy`` 헬퍼 — 한/영 검출과 필터링이 휴리스틱 임계치(min_ratio)
   경계에서 의도대로 동작한다.
2. ``DreamingPipeline._enforce_language_policy`` — 추출된 결과 dict 의 모든
   본문 필드(memory, user_insights, user_insights_meta, active_projects,
   soul/agent_updates) 에서 비-1차 언어 항목이 드롭된다.
3. **DoD 핵심**: 영어 입력 대화에서도 정책이 활성이면 USER.md / MEMORY.md 에
   영어 bullet 이 새지 않는다. 한국어로만 적힌다(또는 영어 bullet 은 드롭).
4. 프롬프트 — 정책 활성 시 LLM 프롬프트에 1차 언어 강제 지시문이 들어가고,
   비활성 시 들어가지 않는다.
5. 비활성 정책(``primary=None``) 은 BIZ-80 이전 동작과 byte-for-byte 동일 —
   영어/한국어 혼재 출력이 그대로 보존된다(레거시 호환성).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.dreaming import DreamingPipeline
from simpleclaw.memory.language_policy import (
    LanguagePolicy,
    filter_active_projects,
    filter_meta_items,
    filter_text_to_primary,
    is_primary_language,
    language_instruction_block,
    split_bullets,
)
from simpleclaw.memory.models import ConversationMessage, MessageRole


# ----------------------------------------------------------------------
# Section 1 — language_policy 모듈 단위 테스트
# ----------------------------------------------------------------------


class TestIsPrimaryLanguage:
    """``is_primary_language`` 의 휴리스틱 경계 동작 검증.

    한국어 문장에는 BIZ-XX, USER.md, GitHub 같은 ASCII 토큰이 자연스럽게 섞인다.
    임계치 0.3 은 그런 정상 한국어 bullet 은 통과시키고, 영어 한 문장만 끼워
    넣은 bullet 은 거절하는 갈라짐 점이다.
    """

    def test_pure_korean_passes(self):
        # 순수 한국어 — 무조건 통과.
        assert is_primary_language("정치 뉴스에 관심이 있다", "ko")

    def test_pure_english_rejected_for_korean(self):
        # 한국어 정책에 영어 한 줄을 들이대면 거절.
        assert not is_primary_language("Likes daily planning", "ko")

    def test_korean_with_acronym_passes(self):
        # "BIZ-66 진행" 같은 정상 한국어 bullet — ASCII 토큰이 끼어 있어도 통과.
        assert is_primary_language("BIZ-66 진행 중", "ko")
        assert is_primary_language("SimpleClaw 빌드 운영자", "ko")

    def test_english_with_korean_token_passes_korean_check(self):
        # 한 단어만 한국어, 나머지는 영어인 경우 — ratio < 0.3 이면 거절.
        # "I want a daily plan 안녕" 의 한글 비율은 약 2/(2+15) ≈ 0.12 → 거절.
        assert not is_primary_language("I want a daily plan 안녕", "ko")

    def test_empty_text_passes(self):
        # 알파벳도 한글도 없는 경우(날짜/숫자/기호) 는 위반 아님으로 통과.
        assert is_primary_language("## 2026-04-28", "ko")
        assert is_primary_language("- 12345", "ko")
        assert is_primary_language("", "ko")

    def test_none_lang_disables_check(self):
        # 정책이 비활성(lang=None) 이면 어떤 입력도 통과.
        assert is_primary_language("Likes daily planning", None)
        assert is_primary_language("정치 뉴스 관심", None)

    def test_min_ratio_zero_passes_anything(self):
        # min_ratio=0 이면 한 글자라도 한글이 있으면 통과(검사 사실상 끔).
        assert is_primary_language("Hello world 안", "ko", min_ratio=0.0)

    def test_min_ratio_strict_rejects_mixed(self):
        # min_ratio=0.7 같은 빡빡한 임계 — "BIZ-66 진행 중" 도 거절될 수 있다.
        # 한글 4자 / (라틴 5 + 한글 4) = 0.44 < 0.7
        assert not is_primary_language("BIZ-66 진행 중", "ko", min_ratio=0.7)

    def test_english_policy_passes_pure_english(self):
        assert is_primary_language("Likes daily planning", "en")
        # 영어 정책에 한국어 bullet 을 들이대면 거절.
        assert not is_primary_language("정치 뉴스 관심", "en")


class TestSplitBullets:
    def test_splits_dash_bullets(self):
        text = "- first\n- second\nplain line"
        out = split_bullets(text)
        assert out == [("- ", "first"), ("- ", "second"), ("", "plain line")]

    def test_preserves_non_bullet_lines(self):
        # 빈 줄 / 헤더 — bullet prefix 없이 통과.
        text = "## Header\n\n- bullet"
        out = split_bullets(text)
        assert out[0] == ("", "## Header")
        assert out[1] == ("", "")
        assert out[2] == ("- ", "bullet")

    def test_handles_indented_bullets(self):
        # 들여쓰기된 bullet 도 prefix 에 들여쓰기 포함되어 인식.
        text = "  - nested"
        out = split_bullets(text)
        assert out[0][0].lstrip() == "- "
        assert out[0][1] == "nested"


class TestFilterTextToPrimary:
    def test_drops_english_bullets_keeps_korean(self):
        text = (
            "- 정치 뉴스에 관심\n"
            "- Likes daily planning\n"
            "- BIZ-66 진행 중"
        )
        kept, dropped = filter_text_to_primary(text, "ko")
        # 한국어 bullet 두 개만 남는다.
        assert "정치 뉴스" in kept
        assert "BIZ-66 진행" in kept
        assert "Likes daily planning" not in kept
        assert dropped == ["Likes daily planning"]

    def test_preserves_headers_and_blank_lines(self):
        # 날짜 헤더와 빈 줄은 검사 없이 통과.
        text = "## 2026-04-28\n\n- 정치 뉴스 관심\n- Likes planning"
        kept, dropped = filter_text_to_primary(text, "ko")
        assert "## 2026-04-28" in kept
        assert "" in kept.splitlines()  # blank line 보존
        assert "Likes planning" not in kept
        assert dropped == ["Likes planning"]

    def test_disabled_policy_returns_input_unchanged(self):
        text = "- 정치 뉴스\n- Likes planning"
        kept, dropped = filter_text_to_primary(text, None)
        assert kept == text
        assert dropped == []

    def test_empty_text_returns_empty(self):
        kept, dropped = filter_text_to_primary("", "ko")
        assert kept == ""
        assert dropped == []


class TestFilterMetaItems:
    def test_drops_items_with_english_topic(self):
        items = [
            {"topic": "정치뉴스", "text": "정치 뉴스 관심"},
            {"topic": "dailyplan", "text": "Likes daily planning"},
            {"topic": "맥북에어가격", "text": "맥북에어 가격 조회"},
        ]
        kept, dropped = filter_meta_items(items, "ko")
        kept_topics = [m["topic"] for m in kept]
        assert "정치뉴스" in kept_topics
        assert "맥북에어가격" in kept_topics
        assert "dailyplan" not in kept_topics
        assert len(dropped) == 1
        assert dropped[0]["topic"] == "dailyplan"

    def test_drops_items_with_korean_topic_but_english_text(self):
        # topic 은 한국어지만 text 가 영어 → 거절.
        items = [
            {"topic": "정치뉴스", "text": "Interested in political news"},
        ]
        kept, dropped = filter_meta_items(items, "ko")
        assert kept == []
        assert len(dropped) == 1

    def test_preserves_empty_items(self):
        # topic/text 가 비어있는 항목은 BIZ-73 검증에서 별도로 처리되므로 통과.
        items = [{"topic": "", "text": ""}, {"topic": "정치뉴스", "text": "관심"}]
        kept, _ = filter_meta_items(items, "ko")
        assert len(kept) == 2

    def test_disabled_policy_returns_all(self):
        items = [{"topic": "x", "text": "Likes daily planning"}]
        kept, dropped = filter_meta_items(items, None)
        assert len(kept) == 1
        assert dropped == []

    def test_skips_non_dict_entries(self):
        # 잘못된 형식은 silently drop (kept 에서 제외, dropped 에도 안 들어감).
        items = ["not a dict", {"topic": "정치뉴스", "text": "관심"}]
        kept, dropped = filter_meta_items(items, "ko")
        assert len(kept) == 1
        assert kept[0]["topic"] == "정치뉴스"


class TestFilterActiveProjects:
    def test_drops_english_role_or_summary(self):
        # name 은 검사 안 함(고유명사 가능). role/recent_summary 는 검사.
        projects = [
            {
                "name": "SimpleClaw",
                "role": "솔로 빌더 — 메모리 파이프라인 개선",
                "recent_summary": "BIZ-80 마무리",
            },
            {
                "name": "OtherProj",
                "role": "Solo builder polishing the memory pipeline",
                "recent_summary": "Wrapping up the language work",
            },
        ]
        kept, dropped = filter_active_projects(projects, "ko")
        assert len(kept) == 1
        assert kept[0]["name"] == "SimpleClaw"
        assert len(dropped) == 1
        assert dropped[0]["name"] == "OtherProj"

    def test_keeps_english_name_with_korean_body(self):
        # name 이 영문 고유명사여도 role/recent_summary 가 한국어면 통과.
        projects = [
            {
                "name": "Multica",
                "role": "플랫폼 빌드 운영자",
                "recent_summary": "BIZ-80 머지 검토",
            }
        ]
        kept, dropped = filter_active_projects(projects, "ko")
        assert len(kept) == 1
        assert dropped == []

    def test_empty_role_and_summary_passes(self):
        projects = [{"name": "X", "role": "", "recent_summary": ""}]
        kept, _ = filter_active_projects(projects, "ko")
        assert len(kept) == 1

    def test_disabled_policy_returns_all(self):
        projects = [{"name": "X", "role": "Solo builder", "recent_summary": "doing stuff"}]
        kept, dropped = filter_active_projects(projects, None)
        assert len(kept) == 1
        assert dropped == []


class TestLanguageInstructionBlock:
    def test_disabled_policy_returns_empty(self):
        # primary=None 이면 프롬프트에 어떤 언어 강제도 들어가지 않는다.
        assert language_instruction_block(LanguagePolicy(primary=None)) == ""

    def test_korean_policy_mentions_korean_and_key_fields(self):
        block = language_instruction_block(LanguagePolicy(primary="ko"))
        assert "한국어" in block
        # DoD: 핵심 출력 필드 이름이 명시되어 LLM 이 실수하지 않게 한다.
        assert "user_insights" in block
        assert "memory" in block
        assert "active_projects" in block

    def test_per_file_overrides_listed(self):
        # AGENT 만 영어로 override → 블록에 그 사실이 명시된다.
        policy = LanguagePolicy(primary="ko", per_file={"agent": "en"})
        block = language_instruction_block(policy)
        assert "한국어" in block
        assert "AGENT" in block
        assert "English" in block

    def test_per_file_override_matching_primary_skipped(self):
        # 같은 언어 override 는 잡음이라 노출하지 않는다.
        policy = LanguagePolicy(primary="ko", per_file={"user": "ko"})
        block = language_instruction_block(policy)
        assert "USER:" not in block


# ----------------------------------------------------------------------
# Section 2 — DreamingPipeline._enforce_language_policy 직접 호출 테스트
# ----------------------------------------------------------------------


@pytest.fixture
def pipeline_with_policy(tmp_path):
    """한국어 정책이 켜진 DreamingPipeline + 마커가 설정된 USER/MEMORY 파일."""
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
        "## Preferences\n"
        "- 1차 언어: 한국어\n"
        "\n"
        "<!-- managed:dreaming:insights -->\n"
        "<!-- /managed:dreaming:insights -->\n"
    )
    pipeline = DreamingPipeline(
        store, memory_file, user_file=user_file,
        language_policy=LanguagePolicy(primary="ko"),
    )
    return store, pipeline, memory_file, user_file


class TestEnforceLanguagePolicy:
    """``_enforce_language_policy`` 가 결과 dict 의 모든 필드에서 영어를 드롭하는지."""

    def test_drops_english_memory_bullets(self, pipeline_with_policy):
        _, pipeline, _, _ = pipeline_with_policy
        result = {
            "memory": (
                "## 2026-05-04\n"
                "- 정치 뉴스 관심\n"
                "- Planned the day"
            ),
            "user_insights": "",
            "user_insights_meta": [],
            "active_projects": [],
        }
        out = pipeline._enforce_language_policy(result)
        assert "정치 뉴스" in out["memory"]
        assert "Planned the day" not in out["memory"]

    def test_drops_english_user_insights(self, pipeline_with_policy):
        _, pipeline, _, _ = pipeline_with_policy
        result = {
            "memory": "",
            "user_insights": "- 정치 뉴스 관심\n- Likes daily planning",
            "user_insights_meta": [],
            "active_projects": [],
        }
        out = pipeline._enforce_language_policy(result)
        assert "정치 뉴스" in out["user_insights"]
        assert "Likes daily planning" not in out["user_insights"]

    def test_drops_english_meta_items(self, pipeline_with_policy):
        _, pipeline, _, _ = pipeline_with_policy
        result = {
            "memory": "",
            "user_insights": "",
            "user_insights_meta": [
                {"topic": "정치뉴스", "text": "정치 뉴스 관심"},
                {"topic": "dailyplan", "text": "Likes daily planning"},
            ],
            "active_projects": [],
        }
        out = pipeline._enforce_language_policy(result)
        topics = [m["topic"] for m in out["user_insights_meta"]]
        assert "정치뉴스" in topics
        assert "dailyplan" not in topics

    def test_drops_english_active_projects(self, pipeline_with_policy):
        _, pipeline, _, _ = pipeline_with_policy
        result = {
            "memory": "",
            "user_insights": "",
            "user_insights_meta": [],
            "active_projects": [
                {
                    "name": "SimpleClaw",
                    "role": "솔로 빌더",
                    "recent_summary": "BIZ-80 마무리",
                },
                {
                    "name": "OtherProj",
                    "role": "Lead engineer",
                    "recent_summary": "Wrapping up the work",
                },
            ],
        }
        out = pipeline._enforce_language_policy(result)
        assert len(out["active_projects"]) == 1
        assert out["active_projects"][0]["name"] == "SimpleClaw"

    def test_drops_english_soul_and_agent_updates(self, pipeline_with_policy):
        _, pipeline, _, _ = pipeline_with_policy
        result = {
            "memory": "",
            "user_insights": "",
            "user_insights_meta": [],
            "active_projects": [],
            "soul_updates": "- 반말 써\n- Use informal tone",
            "agent_updates": "- 캘린더 추가\n- Add calendar",
        }
        out = pipeline._enforce_language_policy(result)
        assert "반말" in out["soul_updates"]
        assert "Use informal tone" not in out["soul_updates"]
        assert "캘린더" in out["agent_updates"]
        assert "Add calendar" not in out["agent_updates"]

    def test_disabled_policy_passes_through(self, tmp_path):
        # primary=None 이면 입력을 그대로 통과 (BIZ-80 이전 동작 호환).
        db = tmp_path / "test.db"
        store = ConversationStore(db)
        memory_file = tmp_path / "MEMORY.md"
        memory_file.write_text(
            "<!-- managed:dreaming:journal -->\n<!-- /managed:dreaming:journal -->\n"
        )
        pipeline = DreamingPipeline(store, memory_file)  # 기본 = primary=None
        result = {
            "memory": "## d\n- Planned the day",
            "user_insights": "- Likes daily planning",
            "user_insights_meta": [{"topic": "x", "text": "Likes planning"}],
            "active_projects": [],
        }
        out = pipeline._enforce_language_policy(result)
        # 정책 비활성 → 영어 항목이 살아 남아야 한다.
        assert "Planned the day" in out["memory"]
        assert "Likes daily planning" in out["user_insights"]
        assert out["user_insights_meta"][0]["topic"] == "x"


# ----------------------------------------------------------------------
# Section 3 — DoD 핵심: 영어 입력 → 한국어 USER.md 통합 시나리오
# ----------------------------------------------------------------------


class TestEnglishInputProducesKoreanOutput:
    """BIZ-80 DoD #5 — 영어 입력 대화에서도 USER.md 가 한국어로만 적힌다.

    실제 LLM은 영어 입력을 받았을 때 영어 또는 한·영 혼합 출력을 낼 수 있다.
    프롬프트에 강제 지시문이 들어가 있어 LLM 이 한국어로 적게 유도하지만,
    LLM 이 지시를 무시해 영어 bullet 을 끼워 넣어도 ``_enforce_language_policy``
    가 자동 드롭해 USER.md 에 영어가 새 나가지 않게 한다.
    """

    @pytest.mark.asyncio
    async def test_english_conversation_korean_user_md(self, pipeline_with_policy):
        store, pipeline, memory_file, user_file = pipeline_with_policy

        # LLM 이 한·영 혼합으로 답한다고 가정 — 한국어 bullet 만 살아남아야 한다.
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "memory": "## 2026-05-04\n- 일일 계획 작성\n- Planned the day",
            "user_insights": "- 일일 계획에 관심\n- Likes daily planning",
            "user_insights_meta": [
                {"topic": "일일계획", "text": "일일 계획에 관심"},
                {"topic": "dailyplan", "text": "Likes daily planning"},
            ],
            "soul_updates": "",
            "agent_updates": "",
            "active_projects": [],
        })
        mock_router = MagicMock()
        mock_router.send = AsyncMock(return_value=mock_response)
        pipeline._router = mock_router

        # 입력 자체는 영어.
        store.add_message(ConversationMessage(
            role=MessageRole.USER, content="Plan my day please"
        ))
        store.add_message(ConversationMessage(
            role=MessageRole.ASSISTANT, content="Here is your schedule"
        ))

        result = await pipeline.run()
        assert result is not None

        # MEMORY.md — 영어 bullet 이 빠지고 한국어 bullet 만 남는다.
        memory_content = memory_file.read_text()
        assert "일일 계획 작성" in memory_content
        assert "Planned the day" not in memory_content

        # USER.md — 영어 인사이트는 드롭, 한국어 인사이트는 보존.
        user_content = user_file.read_text()
        assert "일일 계획에 관심" in user_content
        assert "Likes daily planning" not in user_content

    @pytest.mark.asyncio
    async def test_prompt_contains_language_instruction_when_active(
        self, pipeline_with_policy
    ):
        """정책 활성 시 LLM 프롬프트에 한국어 강제 지시문이 들어간다."""
        store, pipeline, _, _ = pipeline_with_policy

        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "memory": "## d\n- 항목",
            "user_insights": "",
            "user_insights_meta": [],
            "soul_updates": "",
            "agent_updates": "",
            "active_projects": [],
        })
        mock_router = MagicMock()
        mock_router.send = AsyncMock(return_value=mock_response)
        pipeline._router = mock_router

        store.add_message(ConversationMessage(
            role=MessageRole.USER, content="hello"
        ))
        await pipeline.run()

        # send 가 받은 LLMRequest 의 user_message 안에 강제 지시문이 들어 있어야 한다.
        sent_request = mock_router.send.call_args[0][0]
        assert "한국어" in sent_request.user_message
        assert "BIZ-80" in sent_request.user_message

    @pytest.mark.asyncio
    async def test_prompt_omits_instruction_when_policy_disabled(self, tmp_path):
        """정책 비활성(primary=None) 시 프롬프트에 강제 지시문이 들어가지 않는다."""
        db = tmp_path / "test.db"
        store = ConversationStore(db)
        memory_file = tmp_path / "MEMORY.md"
        memory_file.write_text(
            "<!-- managed:dreaming:journal -->\n<!-- /managed:dreaming:journal -->\n"
        )
        # 기본값 = primary=None (레거시 호환).
        pipeline = DreamingPipeline(store, memory_file)

        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "memory": "## d\n- item",
            "user_insights": "",
            "user_insights_meta": [],
            "soul_updates": "",
            "agent_updates": "",
            "active_projects": [],
        })
        mock_router = MagicMock()
        mock_router.send = AsyncMock(return_value=mock_response)
        pipeline._router = mock_router

        store.add_message(ConversationMessage(
            role=MessageRole.USER, content="hello"
        ))
        await pipeline.run()

        sent_request = mock_router.send.call_args[0][0]
        # 정책 비활성 — BIZ-80 강제 지시문 토큰이 없어야 한다.
        assert "BIZ-80" not in sent_request.user_message


# ----------------------------------------------------------------------
# Section 4 — config 통합: load_daemon_config 가 language 블록을 읽어 들이는지
# ----------------------------------------------------------------------


class TestConfigIntegration:
    """``load_daemon_config`` 가 yaml 의 ``daemon.dreaming.language`` 를 정상 로드.

    이 테스트는 BIZ-80 의 wiring 단계 — 운영자가 yaml 만 고쳐도 dreaming
    파이프라인이 새 언어 정책을 받아 들이는지를 확인한다.
    """

    def test_default_language_is_korean(self, tmp_path):
        from simpleclaw.config import load_daemon_config

        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            "daemon:\n"
            "  dreaming:\n"
            "    overnight_hour: 3\n"  # 다른 dreaming 키만 있어도 language 기본이 채워져야 한다.
        )
        cfg = load_daemon_config(cfg_path)
        lang = cfg["dreaming"]["language"]
        assert lang["primary"] == "ko"
        assert 0.0 <= lang["min_ratio"] <= 1.0

    def test_custom_language_loaded(self, tmp_path):
        from simpleclaw.config import load_daemon_config

        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            "daemon:\n"
            "  dreaming:\n"
            "    language:\n"
            "      primary: en\n"
            "      min_ratio: 0.5\n"
            "      per_file:\n"
            "        agent: en\n"
            "        user: ko\n"
        )
        cfg = load_daemon_config(cfg_path)
        lang = cfg["dreaming"]["language"]
        assert lang["primary"] == "en"
        assert lang["min_ratio"] == 0.5
        assert lang["per_file"] == {"agent": "en", "user": "ko"}

    def test_invalid_min_ratio_clamped(self, tmp_path):
        from simpleclaw.config import load_daemon_config

        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            "daemon:\n"
            "  dreaming:\n"
            "    language:\n"
            "      primary: ko\n"
            "      min_ratio: 9.9\n"
        )
        cfg = load_daemon_config(cfg_path)
        # _coerce_language_policy 가 [0, 1] 로 클램프.
        assert 0.0 <= cfg["dreaming"]["language"]["min_ratio"] <= 1.0
