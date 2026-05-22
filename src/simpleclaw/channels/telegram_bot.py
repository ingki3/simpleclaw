"""텔레그램 봇: 폴링 기반 메시지 수신, 화이트리스트 인증, 메시지 핸들링.

python-telegram-bot 라이브러리를 사용하여 텔레그램 메시지를 수신·응답한다.
- 화이트리스트(user_id / chat_id) 기반 접근 제어 (fail-closed 정책)
- 모든 접근 시도를 AccessAttempt으로 기록
- 외부 message_handler 콜백을 주입받아 메시지 처리 위임
"""

from __future__ import annotations

import logging
import re
from typing import Awaitable, Callable

from simpleclaw.channels.models import AccessAttempt

logger = logging.getLogger(__name__)

# Telegram message hard limit (sendMessage rejects payloads > 4096 chars with
# BadRequest: "Message is too long" — no silent truncation, the send fails).
TELEGRAM_MESSAGE_LIMIT = 4096

# 8 chars covers up to "(99/99)\n"; for >99 parts we fall back to a wider
# header but never below this floor so chunks stay aligned with the cap below.
_PROGRESS_HEADER_BUDGET = 8

# ``` 또는 ```lang 형태의 코드 펜스를 줄 시작에서 매치.
_CODE_FENCE_RE = re.compile(r"^```([^\n]*)$", re.MULTILINE)


def _scan_fence_state(
    text: str, start_in_code: bool, start_fence: str
) -> tuple[bool, str]:
    """``text`` 를 ``start_*`` 상태에서 적용한 뒤의 (in_code, fence) 를 돌려준다."""
    in_code = start_in_code
    fence = start_fence
    for m in _CODE_FENCE_RE.finditer(text):
        if in_code:
            in_code = False
            fence = ""
        else:
            in_code = True
            fence = "```" + m.group(1)
    return in_code, fence


def _pick_split_index(head: str, min_ratio: float = 0.5) -> int:
    """``head`` 내에서 자연 경계를 우선해 분할 인덱스를 고른다.

    우선순위: ``\\n\\n`` > ``\\n`` > 공백 > 하드 컷. 너무 앞쪽으로 끊기면
    (전체 길이의 절반 미만) 다음 우선순위 후보를 시도한다.
    """
    min_acceptable = int(len(head) * min_ratio)
    for sep in ("\n\n", "\n", " "):
        idx = head.rfind(sep)
        if idx >= min_acceptable:
            return idx + len(sep)
    return len(head)


def split_for_telegram(
    text: str, limit: int = TELEGRAM_MESSAGE_LIMIT
) -> list[str]:
    """텔레그램 4096자 한계에 맞춰 ``text`` 를 분할한다.

    - 분할이 필요 없으면 ``[text]`` 그대로 돌려준다 (헤더 없음).
    - 분할 시 각 청크에 ``(i/N)\\n`` 진행 헤더를 붙인다.
    - 코드 펜스(```` ``` ````) 중간에서 끊기면 현재 청크는 ```` ``` ```` 로 닫고,
      다음 청크는 동일 펜스(언어 포함)로 다시 열어 가독성을 유지한다.
    - 빈 문자열이면 ``[""]``.
    """
    if len(text) <= limit:
        return [text]

    chunk_budget = limit - _PROGRESS_HEADER_BUDGET
    chunks: list[str] = []
    remaining = text
    in_code = False
    fence = ""

    while remaining:
        # 코드 블록이 열려 있으면 닫기 위해 "\n```" 4자를 비축.
        reserve = 4 if in_code else 0
        budget = chunk_budget - reserve

        if len(remaining) <= budget:
            chunks.append(remaining)
            break

        head = remaining[:budget]
        idx = _pick_split_index(head)

        chunk = remaining[:idx].rstrip("\n")
        rest = remaining[idx:].lstrip("\n")

        end_in_code, end_fence = _scan_fence_state(chunk, in_code, fence)
        if end_in_code:
            chunk = chunk + "\n```"
            reopen = end_fence if end_fence else "```"
            rest = reopen + "\n" + rest
            # 다음 청크는 prepend 된 reopen 펜스로부터 새로 in_code 상태를
            # 진입한다. (prepend 가 곧 열림이므로 시작 상태는 False)
            in_code = False
            fence = ""
        else:
            in_code = end_in_code
            fence = end_fence
        chunks.append(chunk)
        remaining = rest

    total = len(chunks)
    return [f"({i + 1}/{total})\n{c}" for i, c in enumerate(chunks)]


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
                        for part in split_for_telegram(response):
                            await update.message.reply_text(part)

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
