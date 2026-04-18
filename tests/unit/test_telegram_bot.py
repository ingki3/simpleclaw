"""Tests for the Telegram bot."""

import pytest

from simpleclaw.channels.telegram_bot import TelegramBot


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
