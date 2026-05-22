"""Tests for the Telegram bot."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.agent.clarify import (
    ClarifyRequest,
    encode_callback_data,
    normalize_options,
)
from simpleclaw.channels.telegram_bot import (
    TELEGRAM_MESSAGE_LIMIT,
    TelegramBot,
    TelegramStreamSink,
    split_for_telegram,
)


class _FakeBot:
    """python-telegram-bot 의 ``Bot`` 호환 fake — 호출 기록만 누적한다.

    BIZ-259 sink 테스트용. send/edit 콜백은 awaitable 이어야 하므로 ``async def`` 로
    정의하고, 반환값에 ``message_id`` 속성을 흉내내 sink 의 다음 edit 호출에 필요한
    핸들을 만들어준다. ``edit_failures`` 카운트가 0 이상이면 그만큼 edit 이 실패한
    뒤 정상화 — flood-wait / parse-error 시나리오를 흉내.
    """

    def __init__(self, edit_failures: int = 0, send_failures: int = 0):
        self.sent: list[dict] = []
        self.edits: list[dict] = []
        self._edit_failures = edit_failures
        self._send_failures = send_failures
        self._next_id = 100

    async def send_message(self, chat_id, text, **kwargs):
        if self._send_failures > 0:
            self._send_failures -= 1
            raise RuntimeError("simulated send failure")
        msg = SimpleNamespace(message_id=self._next_id, chat_id=chat_id, text=text)
        self._next_id += 1
        self.sent.append({"chat_id": chat_id, "text": text, "kwargs": kwargs})
        return msg

    async def edit_message_text(self, chat_id, message_id, text, **kwargs):
        if self._edit_failures > 0:
            self._edit_failures -= 1
            raise RuntimeError("simulated edit failure")
        self.edits.append(
            {"chat_id": chat_id, "message_id": message_id, "text": text}
        )


class TestTelegramBot:
    def test_authorize_whitelisted_user(self):
        bot = TelegramBot("token", whitelist_user_ids=[123, 456])
        assert bot.is_authorized(123, 999) is True
        assert bot.is_authorized(456, 999) is True

    def test_reject_non_whitelisted_user(self):
        bot = TelegramBot("token", whitelist_user_ids=[123])
        assert bot.is_authorized(789, 999) is False

    def test_authorize_whitelisted_chat(self):
        bot = TelegramBot("token", whitelist_chat_ids=[100])
        assert bot.is_authorized(999, 100) is True

    def test_fail_closed_no_whitelist(self):
        bot = TelegramBot("token")
        assert bot.is_authorized(123, 456) is False

    def test_fail_closed_empty_whitelist(self):
        bot = TelegramBot("token", whitelist_user_ids=[], whitelist_chat_ids=[])
        assert bot.is_authorized(123, 456) is False

    @pytest.mark.asyncio
    async def test_handle_authorized_message(self):
        bot = TelegramBot("token", whitelist_user_ids=[123])
        response = await bot.handle_message("Hello", 123, 999)
        assert response is not None
        assert "Hello" in response

    @pytest.mark.asyncio
    async def test_handle_unauthorized_message(self):
        bot = TelegramBot("token", whitelist_user_ids=[123])
        response = await bot.handle_message("Hello", 789, 999)
        assert response is None

    @pytest.mark.asyncio
    async def test_access_log(self):
        bot = TelegramBot("token", whitelist_user_ids=[123])
        await bot.handle_message("test", 123, 999)
        await bot.handle_message("test", 789, 999)
        log = bot.get_access_log()
        assert len(log) == 2
        assert log[0].authorized is True
        assert log[1].authorized is False

    @pytest.mark.asyncio
    async def test_custom_message_handler(self):
        async def handler(text, user_id, chat_id):
            return f"Custom: {text}"

        bot = TelegramBot("token", whitelist_user_ids=[1], message_handler=handler)
        response = await bot.handle_message("test", 1, 1)
        assert response == "Custom: test"

    @pytest.mark.asyncio
    async def test_long_message_truncation(self):
        bot = TelegramBot("token", whitelist_user_ids=[1])
        long_msg = "x" * 10000
        response = await bot.handle_message(long_msg, 1, 1)
        assert response is not None
        # The response should contain truncated message
        assert len(response) < 10000


class TestSplitForTelegram:
    def test_short_message_returns_single_chunk_no_header(self):
        text = "hello"
        assert split_for_telegram(text) == ["hello"]

    def test_empty_string_returns_single_empty_chunk(self):
        assert split_for_telegram("") == [""]

    def test_exact_limit_not_split(self):
        text = "x" * TELEGRAM_MESSAGE_LIMIT
        result = split_for_telegram(text)
        assert len(result) == 1
        assert result[0] == text

    def test_8000_chars_splits_into_two_with_progress_header(self):
        text = "x" * 8000
        result = split_for_telegram(text)
        assert len(result) == 2
        assert result[0].startswith("(1/2)\n")
        assert result[1].startswith("(2/2)\n")
        # 모든 청크는 텔레그램 한계 이하여야 함.
        for part in result:
            assert len(part) <= TELEGRAM_MESSAGE_LIMIT
        # 청크에서 헤더를 제거하면 원본이 보존돼야 함.
        rebuilt = "".join(part.split("\n", 1)[1] for part in result)
        assert rebuilt == text

    def test_split_prefers_paragraph_boundary(self):
        paragraph = ("word " * 800).rstrip()  # ~4000 chars
        text = paragraph + "\n\n" + paragraph
        result = split_for_telegram(text)
        assert len(result) == 2
        # 두 번째 청크는 두 번째 문단으로 시작해야 한다 (헤더 제거 후).
        body2 = result[1].split("\n", 1)[1]
        assert body2 == paragraph

    def test_codeblock_preserved_across_split(self):
        # 8000자 코드블록 한 덩어리 — 강제로 중간 분할.
        body = "print('x')\n" * 600  # ~6600 chars
        text = "intro\n```python\n" + body + "```\nouter"
        result = split_for_telegram(text)
        assert len(result) >= 2
        # 첫 청크는 ``` 로 닫혀 있어야 함 (마지막 비공백 라인).
        first_body = result[0].split("\n", 1)[1]
        assert first_body.rstrip().endswith("```"), (
            "first chunk should close the code fence so it renders cleanly"
        )
        # 두 번째 청크는 동일 언어(```python)로 다시 열려야 함.
        second_body = result[1].split("\n", 1)[1]
        assert second_body.startswith("```python\n"), (
            "second chunk should reopen the fence with the same language"
        )

    def test_no_progress_header_when_only_one_chunk(self):
        text = "x" * (TELEGRAM_MESSAGE_LIMIT - 10)
        result = split_for_telegram(text)
        assert result == [text]
        assert not result[0].startswith("(")

    def test_all_chunks_within_limit_for_huge_input(self):
        text = ("paragraph body. " * 50 + "\n\n") * 60  # ~50000 chars
        result = split_for_telegram(text)
        assert len(result) > 1
        for part in result:
            assert len(part) <= TELEGRAM_MESSAGE_LIMIT


# ----------------------------------------------------------------------
# BIZ-260 — clarify 인라인 키보드 + callback_query 화이트리스트
# ----------------------------------------------------------------------


def _build_clarify_request(*option_bodies: str) -> ClarifyRequest:
    return ClarifyRequest(
        question="Which one?",
        options=normalize_options(list(option_bodies)),
    )


def _mock_query(
    *, user_id: int, chat_id: int, message_id: int, data: str
):
    """python-telegram-bot 의 ``CallbackQuery`` 형상을 흉내내는 mock 객체.

    실제 라이브러리 의존 없이 ``_on_callback_query`` 의 흐름을 검증한다.
    """
    query = MagicMock()
    query.from_user = MagicMock()
    query.from_user.id = user_id
    query.message = MagicMock()
    query.message.chat_id = chat_id
    query.message.message_id = message_id
    query.message.reply_text = AsyncMock()
    query.data = data
    query.answer = AsyncMock()
    return query


class TestClarifyKeyboardRendering:
    @pytest.mark.asyncio
    async def test_send_response_renders_inline_keyboard_when_pending(self):
        request = _build_clarify_request("Foo", "Bar")
        bot = TelegramBot(
            "token",
            whitelist_user_ids=[123],
            clarify_provider=lambda chat_id: request,
        )

        sent_message = MagicMock()
        sent_message.message_id = 5001
        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock(return_value=sent_message)

        # The library's InlineKeyboardMarkup needs python-telegram-bot installed —
        # the repo declares it as a runtime dep, so import succeeds at test time.
        await bot._send_response(update, "unused response", chat_id=42)

        # 응답 본문은 질문 (옵션 라벨이 키보드로 빠진다).
        update.message.reply_text.assert_awaited_once()
        kwargs = update.message.reply_text.await_args.kwargs
        assert update.message.reply_text.await_args.args[0] == "Which one?"
        assert kwargs.get("reply_markup") is not None
        # 옵션 캐시에 본문이 적재됐어야 한다.
        assert bot._clarify_cache[(42, 5001)] == request.options

    @pytest.mark.asyncio
    async def test_send_response_falls_back_to_text_when_no_clarify(self):
        bot = TelegramBot(
            "token",
            whitelist_user_ids=[123],
            clarify_provider=lambda chat_id: None,
        )
        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        await bot._send_response(update, "plain text", chat_id=42)
        update.message.reply_text.assert_awaited_once_with("plain text")

    def test_clarify_cache_lru_eviction(self):
        from simpleclaw.channels.telegram_bot import (
            _CLARIFY_CACHE_MAX_ENTRIES,
        )

        bot = TelegramBot("token", whitelist_user_ids=[1])
        opts = normalize_options(["A"])

        # 한계의 +5 개 입력 → 가장 오래된 5개가 evict 되어야 한다.
        for mid in range(_CLARIFY_CACHE_MAX_ENTRIES + 5):
            bot._cache_clarify_options(1, mid, opts)
        assert len(bot._clarify_cache) == _CLARIFY_CACHE_MAX_ENTRIES
        # 오래된 (1, 0..4) 키는 빠지고 최근 (1, max..max+4) 가 남아야 한다.
        for evicted in range(5):
            assert (1, evicted) not in bot._clarify_cache
        for kept in range(5, _CLARIFY_CACHE_MAX_ENTRIES + 5):
            assert (1, kept) in bot._clarify_cache


class TestCallbackQueryWhitelist:
    @pytest.mark.asyncio
    async def test_unauthorized_callback_silently_dropped(self):
        """화이트리스트 외부 사용자의 callback_query 는 silent drop + 로그."""
        handler = AsyncMock(return_value="should not be called")
        bot = TelegramBot(
            "token",
            whitelist_user_ids=[123],
            message_handler=handler,
        )
        opts = normalize_options(["A", "B"])
        bot._cache_clarify_options(999, 5, opts)

        query = _mock_query(
            user_id=789,  # not in whitelist
            chat_id=999,
            message_id=5,
            data=encode_callback_data(0),
        )
        update = MagicMock()
        update.callback_query = query

        await bot._on_callback_query(update, MagicMock())

        # message_handler 는 호출되지 않아야 한다 (보안 회귀 면).
        handler.assert_not_called()
        # spinner 제거용 빈 answer 만 호출.
        query.answer.assert_awaited_once_with()
        # 비인가 로그가 access log 에 남아야 한다.
        assert any(not a.authorized for a in bot.get_access_log())

    @pytest.mark.asyncio
    async def test_authorized_callback_dispatches_option_body(self):
        handler = AsyncMock(return_value="ok")
        bot = TelegramBot(
            "token",
            whitelist_user_ids=[123],
            message_handler=handler,
            clarify_provider=lambda chat_id: None,
        )
        opts = normalize_options(["Foo body", "Bar body"])
        bot._cache_clarify_options(999, 5, opts)

        query = _mock_query(
            user_id=123,
            chat_id=999,
            message_id=5,
            data=encode_callback_data(1),
        )
        update = MagicMock()
        update.callback_query = query

        await bot._on_callback_query(update, MagicMock())

        # 선택된 옵션 본문이 message_handler 로 흘러야 한다.
        handler.assert_awaited_once_with("Bar body", 123, 999)
        query.answer.assert_awaited()

    @pytest.mark.asyncio
    async def test_expired_or_missing_cache_entry_shows_toast(self):
        handler = AsyncMock()
        bot = TelegramBot(
            "token",
            whitelist_user_ids=[123],
            message_handler=handler,
        )

        query = _mock_query(
            user_id=123,
            chat_id=999,
            message_id=42,  # never cached
            data=encode_callback_data(0),
        )
        update = MagicMock()
        update.callback_query = query

        await bot._on_callback_query(update, MagicMock())

        handler.assert_not_called()
        # 만료 안내가 사용자에게 노출되어야 한다.
        query.answer.assert_awaited()
        kwargs = query.answer.await_args.kwargs
        assert "만료" in kwargs.get("text", "")

    @pytest.mark.asyncio
    async def test_invalid_callback_data_dropped(self):
        handler = AsyncMock()
        bot = TelegramBot(
            "token",
            whitelist_user_ids=[123],
            message_handler=handler,
        )
        opts = normalize_options(["A"])
        bot._cache_clarify_options(999, 5, opts)

        query = _mock_query(
            user_id=123,
            chat_id=999,
            message_id=5,
            data="not:a:c:payload",
        )
        update = MagicMock()
        update.callback_query = query

        await bot._on_callback_query(update, MagicMock())

        handler.assert_not_called()
        query.answer.assert_awaited()


class TestTelegramStreamSink:
    """BIZ-259 — LLM 응답 점진 스트리밍 sink."""

    @pytest.mark.asyncio
    async def test_start_sends_placeholder_and_captures_message_id(self):
        fake = _FakeBot()
        sink = TelegramStreamSink(
            bot=fake, chat_id=42, initial_placeholder="…",
        )
        await sink.start()
        assert len(fake.sent) == 1
        assert fake.sent[0]["chat_id"] == 42
        assert fake.sent[0]["text"] == "…"
        assert sink.message_id == 100

    @pytest.mark.asyncio
    async def test_min_interval_buffers_until_window_elapses(self):
        fake = _FakeBot()
        sink = TelegramStreamSink(
            bot=fake, chat_id=1,
            min_interval_ms=500, min_delta_chars=1,  # delta gate off
        )
        await sink.start()
        # 첫 델타 — 직후 window 미경과로 edit 안 됨.
        await sink.on_text_delta("hello")
        assert fake.edits == []
        # min_interval 경과를 흉내내기 위해 sink 내부 last_edit_ts 를 과거로 당긴다.
        sink._last_edit_ts = time.monotonic() - 1.0
        await sink.on_text_delta(" world")
        assert len(fake.edits) == 1
        assert fake.edits[0]["text"] == "hello world"

    @pytest.mark.asyncio
    async def test_min_delta_chars_buffers_until_threshold(self):
        fake = _FakeBot()
        sink = TelegramStreamSink(
            bot=fake, chat_id=1,
            min_interval_ms=0, min_delta_chars=10,
        )
        await sink.start()
        # 누적 9자 — 임계치 미달, edit 안 됨.
        await sink.on_text_delta("123456789")
        assert fake.edits == []
        # 1자 추가 → 누적 10자 / placeholder("…", 1자) 대비 9자 차이는 여전히 임계 미달.
        # → 한 글자 더 추가해 차이가 10이 되도록 한다.
        await sink.on_text_delta("a")  # 누적 10
        assert fake.edits == []
        await sink.on_text_delta("b")  # 누적 11 — 차이 10 → edit
        assert len(fake.edits) == 1
        assert fake.edits[0]["text"] == "1234567890ab"[:11] or fake.edits[0]["text"] == "123456789ab"

    @pytest.mark.asyncio
    async def test_finalize_short_text_edits_placeholder_once(self):
        fake = _FakeBot()
        sink = TelegramStreamSink(
            bot=fake, chat_id=7, min_interval_ms=10000, min_delta_chars=1000,
        )
        await sink.start()
        # 게이트 통과 안 되는 작은 델타들 — buffer 만 누적.
        await sink.on_text_delta("partial")
        assert fake.edits == []
        sent = await sink.finalize("final answer")
        # finalize 는 placeholder 를 edit 해서 final 로 교체.
        assert sent == ["final answer"]
        assert len(fake.edits) == 1
        assert fake.edits[-1]["text"] == "final answer"
        # 추가 send 는 없어야 함 (단일 청크).
        assert len(fake.sent) == 1  # placeholder 만

    @pytest.mark.asyncio
    async def test_finalize_oversized_splits_via_biz253(self):
        fake = _FakeBot()
        sink = TelegramStreamSink(bot=fake, chat_id=5)
        await sink.start()
        long_text = "x" * 8000
        sent = await sink.finalize(long_text)
        # BIZ-253 분할 — 2 청크. 첫 청크는 edit, 두 번째는 send.
        assert len(sent) == 2
        assert sent[0].startswith("(1/2)\n")
        assert sent[1].startswith("(2/2)\n")
        assert len(fake.edits) == 1  # 첫 청크
        assert len(fake.sent) == 2  # placeholder + 두 번째 청크
        for chunk in sent:
            assert len(chunk) <= TELEGRAM_MESSAGE_LIMIT

    @pytest.mark.asyncio
    async def test_finalize_idempotent(self):
        """finalize 두 번 호출해도 두 번째는 no-op (메시지 중복 방지)."""
        fake = _FakeBot()
        sink = TelegramStreamSink(bot=fake, chat_id=9)
        await sink.start()
        first = await sink.finalize("answer")
        second = await sink.finalize("answer again")
        assert first == ["answer"]
        assert second == []
        # 'answer again' 이 어디에도 누설되지 않아야 함.
        assert all("again" not in e["text"] for e in fake.edits)
        assert all("again" not in s["text"] for s in fake.sent)

    @pytest.mark.asyncio
    async def test_edit_failure_does_not_abort_stream(self):
        """텔레그램 API 가 일시 거부하면 (FloodWait 등) WARN 만 남기고 buffer 는 살아있다."""
        fake = _FakeBot(edit_failures=1)
        sink = TelegramStreamSink(
            bot=fake, chat_id=3,
            min_interval_ms=0, min_delta_chars=1,
        )
        await sink.start()
        # 첫 edit 시도는 실패 (시뮬). buffer/accumulator 는 보존.
        await sink.on_text_delta("hello")
        assert sink.accumulated_text == "hello"
        # 다음 시도는 성공해야 한다.
        sink._last_edit_ts = time.monotonic() - 1.0
        await sink.on_text_delta(" world")
        assert len(fake.edits) == 1
        assert fake.edits[0]["text"] == "hello world"

    @pytest.mark.asyncio
    async def test_finalize_empty_response_shows_explicit_marker(self):
        """빈 응답이 들어와도 placeholder 가 남지 않도록 명시적 메시지로 교체."""
        fake = _FakeBot()
        sink = TelegramStreamSink(bot=fake, chat_id=11)
        await sink.start()
        sent = await sink.finalize("")
        assert sent and "빈 응답" in sent[0]

    @pytest.mark.asyncio
    async def test_overflow_during_stream_truncates_then_finalize_splits(self):
        """4096 초과 누적 — stream 중에는 truncate, finalize 에서 자연 분할."""
        fake = _FakeBot()
        sink = TelegramStreamSink(
            bot=fake, chat_id=2,
            min_interval_ms=0, min_delta_chars=1,
        )
        await sink.start()
        # 5000자 한 번에 흘려보내기 — edit 호출 시점에 truncate 가 적용된다.
        await sink.on_text_delta("y" * 5000)
        assert fake.edits[-1]["text"].endswith("…")
        assert len(fake.edits[-1]["text"]) <= TELEGRAM_MESSAGE_LIMIT
        # finalize 에서는 BIZ-253 분할.
        sent = await sink.finalize("y" * 5000)
        assert len(sent) == 2  # 5000 → 2 청크
        for chunk in sent:
            assert len(chunk) <= TELEGRAM_MESSAGE_LIMIT


class TestTelegramBotStreaming:
    """BIZ-259 — TelegramBot 의 handle_message 가 on_text_delta 콜백을 흘려보낸다."""

    @pytest.mark.asyncio
    async def test_handle_message_forwards_on_text_delta(self):
        deltas_seen: list[str] = []

        async def handler(text, user_id, chat_id, *, on_text_delta=None):
            assert on_text_delta is not None
            await on_text_delta("hello ")
            await on_text_delta("world")
            return "hello world"

        async def sink_cb(delta: str) -> None:
            deltas_seen.append(delta)

        bot = TelegramBot(
            "token", whitelist_user_ids=[1], message_handler=handler,
        )
        response = await bot.handle_message(
            "ping", 1, 1, on_text_delta=sink_cb,
        )
        assert response == "hello world"
        assert deltas_seen == ["hello ", "world"]

    @pytest.mark.asyncio
    async def test_handle_message_no_callback_path_unchanged(self):
        """on_text_delta 미지정 시 기존 핸들러 시그니처(3-arg) 와 회귀 0."""
        async def handler(text, user_id, chat_id):
            return f"got {text}"

        bot = TelegramBot(
            "token", whitelist_user_ids=[1], message_handler=handler,
        )
        response = await bot.handle_message("ping", 1, 1)
        assert response == "got ping"
