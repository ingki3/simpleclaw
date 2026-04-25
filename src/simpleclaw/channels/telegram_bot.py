"""텔레그램 봇: 폴링 기반 메시지 수신, 화이트리스트 인증, 메시지 핸들링.

python-telegram-bot 라이브러리를 사용하여 텔레그램 메시지를 수신·응답한다.
- 화이트리스트(user_id / chat_id) 기반 접근 제어 (fail-closed 정책)
- 모든 접근 시도를 AccessAttempt으로 기록
- 외부 message_handler 콜백을 주입받아 메시지 처리 위임
"""

from __future__ import annotations

import logging
from typing import Callable, Awaitable

from simpleclaw.channels.models import AccessAttempt

logger = logging.getLogger(__name__)


class TelegramBot:
    """화이트리스트 기반 접근 제어를 갖춘 텔레그램 봇.

    python-telegram-bot 라이브러리의 폴링 모드를 사용한다.
    화이트리스트가 비어 있으면 모든 메시지를 거부하는 fail-closed 정책을 따른다.
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
        """사용자/채팅이 화이트리스트에 포함되어 있는지 확인한다.

        Fail-closed 정책: 화이트리스트가 설정되지 않으면 모든 메시지를 거부한다.
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
        """접근 시도를 로그에 기록한다. 비인가 접근 시 경고 로그를 남긴다."""
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
        """접근 시도 로그의 복사본을 반환한다."""
        return list(self._access_log)

    async def handle_message(
        self, text: str, user_id: int, chat_id: int
    ) -> str | None:
        """수신 메시지를 인증 후 처리한다.

        비인가 사용자이면 None을 반환하고, 핸들러가 없으면 에코 응답을 보낸다.
        """
        if not self.is_authorized(user_id, chat_id):
            self.log_access(user_id, chat_id, authorized=False)
            return None

        self.log_access(user_id, chat_id, authorized=True)

        # 텔레그램 메시지 최대 길이 제한 (4096자)
        text = text[:4096] if len(text) > 4096 else text

        if self._message_handler:
            try:
                return await self._message_handler(text, user_id, chat_id)
            except Exception:
                logger.exception("Message handler error")
                return "An error occurred while processing your message."

        # 기본 동작: 메시지 에코
        logger.info(
            "Telegram message from user=%d: %s",
            user_id,
            text[:100],
        )
        return f"Received: {text[:200]}"

    async def start(self) -> None:
        """텔레그램 봇 폴링을 시작한다."""
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
        """텔레그램 봇을 정지한다."""
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
