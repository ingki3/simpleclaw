"""Telegram bot: polling, whitelist authentication, message handling."""

from __future__ import annotations

import logging
from typing import Callable, Awaitable

from simpleclaw.channels.models import AccessAttempt

logger = logging.getLogger(__name__)


class TelegramBot:
    """Telegram bot integration with whitelist-based access control.

    Uses python-telegram-bot library for polling.
    """

    def __init__(
        self,
        bot_token: str,
        whitelist_user_ids: list[int] | None = None,
        whitelist_chat_ids: list[int] | None = None,
        message_handler: Callable[[str, int, int], Awaitable[str]] | None = None,
    ) -> None:
        self._bot_token = bot_token
        self._whitelist_user_ids = set(whitelist_user_ids or [])
        self._whitelist_chat_ids = set(whitelist_chat_ids or [])
        self._message_handler = message_handler
        self._application = None
        self._running = False
        self._access_log: list[AccessAttempt] = []

    def is_authorized(self, user_id: int, chat_id: int) -> bool:
        """Check if a user/chat is authorized via whitelist.

        Fail-closed: if no whitelist is configured, all messages are rejected.
        """
        if not self._whitelist_user_ids and not self._whitelist_chat_ids:
            return False

        if user_id in self._whitelist_user_ids:
            return True
        if chat_id in self._whitelist_chat_ids:
            return True

        return False

    def log_access(
        self, user_id: int, chat_id: int, authorized: bool
    ) -> AccessAttempt:
        attempt = AccessAttempt(
            source="telegram",
            user_identifier=f"user:{user_id}/chat:{chat_id}",
            authorized=authorized,
        )
        self._access_log.append(attempt)
        if not authorized:
            logger.warning(
                "Unauthorized Telegram access: user=%d, chat=%d",
                user_id,
                chat_id,
            )
        return attempt

    def get_access_log(self) -> list[AccessAttempt]:
        return list(self._access_log)

    async def handle_message(
        self, text: str, user_id: int, chat_id: int
    ) -> str | None:
        """Process an incoming message after authorization."""
        if not self.is_authorized(user_id, chat_id):
            self.log_access(user_id, chat_id, authorized=False)
            return None

        self.log_access(user_id, chat_id, authorized=True)

        # Truncate very long messages
        text = text[:4096] if len(text) > 4096 else text

        if self._message_handler:
            try:
                return await self._message_handler(text, user_id, chat_id)
            except Exception:
                logger.exception("Message handler error")
                return "An error occurred while processing your message."

        # Default: echo the message
        logger.info(
            "Telegram message from user=%d: %s",
            user_id,
            text[:100],
        )
        return f"Received: {text[:200]}"

    async def start(self) -> None:
        """Start the Telegram bot polling."""
        if not self._bot_token:
            logger.warning("Telegram bot token not configured. Skipping.")
            return

        try:
            from telegram.ext import (
                ApplicationBuilder,
                MessageHandler,
                filters,
            )

            self._application = (
                ApplicationBuilder()
                .token(self._bot_token)
                .build()
            )

            async def _on_message(update, context):
                if update.message and update.message.text:
                    user_id = update.message.from_user.id
                    chat_id = update.message.chat_id
                    response = await self.handle_message(
                        update.message.text, user_id, chat_id
                    )
                    if response:
                        await update.message.reply_text(response)

            self._application.add_handler(
                MessageHandler(filters.TEXT, _on_message)
            )

            await self._application.initialize()
            await self._application.start()
            await self._application.updater.start_polling()
            self._running = True
            logger.info("Telegram bot started polling.")

        except ImportError:
            logger.error(
                "python-telegram-bot not installed. Telegram integration disabled."
            )
        except Exception:
            logger.exception("Failed to start Telegram bot")

    async def stop(self) -> None:
        """Stop the Telegram bot."""
        if self._application and self._running:
            try:
                await self._application.updater.stop()
                await self._application.stop()
                await self._application.shutdown()
                self._running = False
                logger.info("Telegram bot stopped.")
            except Exception:
                logger.exception("Error stopping Telegram bot")

    @property
    def is_running(self) -> bool:
        return self._running
