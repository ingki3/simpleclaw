"""Tests for the Telegram bot."""

import pytest

from simpleclaw.channels.telegram_bot import (
    TELEGRAM_MESSAGE_LIMIT,
    TelegramBot,
    split_for_telegram,
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
