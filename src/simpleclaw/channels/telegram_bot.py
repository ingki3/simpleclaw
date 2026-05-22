"""텔레그램 봇: 폴링 기반 메시지 수신, 화이트리스트 인증, 메시지 핸들링.

python-telegram-bot 라이브러리를 사용하여 텔레그램 메시지를 수신·응답한다.
- 화이트리스트(user_id / chat_id) 기반 접근 제어 (fail-closed 정책)
- 모든 접근 시도를 AccessAttempt으로 기록
- 외부 message_handler 콜백을 주입받아 메시지 처리 위임
- BIZ-260: ReAct 에이전트의 ``clarify`` 도구가 호출되면 인라인 키보드 버튼으로
  옵션을 렌더 + ``callback_query`` 핸들러가 인바운드 메시지와 동일한 화이트리스트
  를 재검증한 뒤 선택지를 새 메시지로 주입.
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from typing import Awaitable, Callable

from simpleclaw.agent.clarify import (
    ClarifyOption,
    ClarifyRequest,
    decode_callback_data,
    encode_callback_data,
)
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

# BIZ-260 — clarify 옵션 캐시 LRU 한계. (chat_id, message_id) → list[ClarifyOption].
# 봇 프로세스 메모리에만 존재하므로 재시작 시 휘발 (Hermes 패턴 그대로).
# 100개면 최근 ~100 차례의 clarify 질문을 콜백 가능 상태로 유지 — 일반 대화량
# 기준 수일~수주 분량. 한계 초과 시 가장 오래된 항목부터 evict.
_CLARIFY_CACHE_MAX_ENTRIES = 100


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
        clarify_provider: Callable[[int], ClarifyRequest | None] | None = None,
    ) -> None:
        self._bot_token = bot_token
        self._whitelist_user_ids = set(whitelist_user_ids or [])
        self._whitelist_chat_ids = set(whitelist_chat_ids or [])
        self._message_handler = message_handler
        # BIZ-260: clarify 도구가 발생시킨 ClarifyRequest 를 회수하는 콜백.
        # ``AgentOrchestrator.pop_pending_clarify`` 를 그대로 주입하면 된다.
        # None 이면 인라인 키보드 렌더 경로가 꺼져 기존 텍스트 응답만 동작 — 봇 호환
        # 모드(테스트, 다른 채널) 에서 의존성을 줄이기 위한 옵셔널.
        self._clarify_provider = clarify_provider
        # BIZ-260: (chat_id, message_id) → list[ClarifyOption] LRU 캐시.
        # 콜백 페이로드에는 옵션 인덱스만 실리고, 본문은 여기서 조회한다.
        self._clarify_cache: OrderedDict[
            tuple[int, int], list[ClarifyOption]
        ] = OrderedDict()
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

    # ------------------------------------------------------------------
    # BIZ-260 — clarify 인라인 키보드
    # ------------------------------------------------------------------

    def _cache_clarify_options(
        self, chat_id: int, message_id: int, options: list[ClarifyOption]
    ) -> None:
        """``(chat_id, message_id)`` 키에 옵션 본문을 적재한다 (LRU).

        같은 키가 이미 있으면 새 옵션으로 덮어쓰고 LRU 위치는 최신화. 한계를
        넘으면 가장 오래된 항목을 제거. 캐시는 프로세스 메모리에만 존재하므로
        봇 재시작 시 휘발한다 (Hermes 패턴 — DoD 와 일치).
        """
        key = (chat_id, message_id)
        if key in self._clarify_cache:
            self._clarify_cache.move_to_end(key)
        self._clarify_cache[key] = options
        while len(self._clarify_cache) > _CLARIFY_CACHE_MAX_ENTRIES:
            self._clarify_cache.popitem(last=False)

    def _lookup_clarify_option(
        self, chat_id: int, message_id: int, option_index: int
    ) -> ClarifyOption | None:
        """캐시에서 (chat_id, message_id, option_index) 에 해당하는 옵션을 회수.

        존재하지 않거나 인덱스 범위 밖이면 None — 호출자는 silently drop 한다
        (메시지가 너무 오래되어 evict 됐거나, 위조 callback_data).
        """
        options = self._clarify_cache.get((chat_id, message_id))
        if options is None:
            return None
        if option_index < 0 or option_index >= len(options):
            return None
        # 최근 사용 → LRU 위치 갱신.
        self._clarify_cache.move_to_end((chat_id, message_id))
        return options[option_index]

    def _build_inline_keyboard(self, request: ClarifyRequest):
        """``ClarifyRequest`` 로부터 InlineKeyboardMarkup 을 생성.

        한 줄당 한 버튼 — 옵션 본문이 모바일 폭을 넘는 경우가 많고, Hermes 도 동일
        레이아웃. import 는 함수 안에서 — python-telegram-bot 미설치 환경에서도
        모듈 import 가 깨지지 않는다 (테스트 격리).
        """
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        rows = [
            [InlineKeyboardButton(opt.label, callback_data=encode_callback_data(opt.index))]
            for opt in request.options
        ]
        return InlineKeyboardMarkup(rows)

    async def _send_response(
        self, update, response: str, *, chat_id: int
    ) -> None:
        """일반 텍스트 응답 또는 clarify 인라인 키보드를 보낸다.

        ``clarify_provider`` 가 ``ClarifyRequest`` 를 돌려주면 응답 텍스트는 무시
        하고 질문 + 키보드만 송신 (사용자에게 깔끔한 다지선다 UI 노출).
        ``response`` 가 비어 있거나 None 이고 clarify 도 없으면 무응답 — 호출자는
        이미 ``handle_message`` 가 비인가/오류로 None 을 돌려준 분기를 분리해
        처리해야 한다.
        """
        request: ClarifyRequest | None = None
        if self._clarify_provider is not None:
            try:
                request = self._clarify_provider(chat_id)
            except Exception:
                logger.exception("clarify_provider raised; falling back to text")
                request = None

        if request is not None:
            keyboard = self._build_inline_keyboard(request)
            sent = await update.message.reply_text(
                request.question[:TELEGRAM_MESSAGE_LIMIT],
                reply_markup=keyboard,
            )
            self._cache_clarify_options(
                chat_id, sent.message_id, request.options,
            )
            logger.info(
                "Telegram clarify sent: chat=%d msg=%d options=%d",
                chat_id, sent.message_id, len(request.options),
            )
            return

        if not response:
            return
        for part in split_for_telegram(response):
            await update.message.reply_text(part)

    async def _on_callback_query(self, update, context) -> None:
        """인라인 키보드 버튼 탭 → 화이트리스트 재검증 → 옵션 본문 주입.

        BIZ-260 DoD:
        - 인바운드 메시지와 동일한 화이트리스트(``is_authorized``) 가드를 적용 — 외부
          사용자가 누군가 받은 메시지의 콜백 ID 를 위조해 보내도 silently drop.
        - 캐시에 옵션이 없거나(만료/evict) callback_data 가 위조됐으면 사용자 보호용
          토스트(``"이 질문은 만료되었습니다"``) 만 띄우고 종료.
        - 매칭된 옵션 본문을 ``handle_message`` 로 다시 흘려보내, 텍스트 응답 경로와
          완전히 동일한 ReAct 루프를 거치도록 한다 — 코드 한 곳에서 ReAct 입력 가공.
        """
        query = getattr(update, "callback_query", None)
        if query is None:
            return

        from_user = getattr(query, "from_user", None)
        message = getattr(query, "message", None)
        if from_user is None or message is None:
            return

        user_id = from_user.id
        chat_id = message.chat_id
        message_id = message.message_id
        data = query.data or ""

        # 화이트리스트 재검증 — 보안 회귀 면 (DoD #3). 인바운드 텍스트와 동일 가드.
        if not self.is_authorized(user_id, chat_id):
            self.log_access(user_id, chat_id, authorized=False)
            # 외부에서 인지하지 못하도록 silent drop. answer() 로 spinner 만 제거.
            try:
                await query.answer()
            except Exception:
                logger.debug("callback_query.answer() failed for unauthorized user")
            return

        self.log_access(user_id, chat_id, authorized=True)

        option_index = decode_callback_data(data)
        if option_index is None:
            # 위조된 prefix / 정수 아님 — 사용자에게 토스트로 알릴 가치 없음.
            try:
                await query.answer()
            except Exception:
                logger.debug("callback_query.answer() failed for invalid payload")
            logger.warning(
                "Unparseable clarify callback_data: chat=%d user=%d data=%r",
                chat_id, user_id, data[:64],
            )
            return

        option = self._lookup_clarify_option(chat_id, message_id, option_index)
        if option is None:
            try:
                await query.answer(
                    text="이 질문은 만료되었거나 옵션을 찾을 수 없습니다.",
                    show_alert=False,
                )
            except Exception:
                logger.debug("callback_query.answer() failed for expired option")
            return

        # 사용자에게 즉시 피드백 + spinner 제거.
        try:
            await query.answer(text=f"선택: {option.label}", show_alert=False)
        except Exception:
            logger.debug("callback_query.answer() failed (selected option)")

        logger.info(
            "Telegram clarify callback: chat=%d user=%d msg=%d option=%d",
            chat_id, user_id, message_id, option_index,
        )

        # 옵션 본문을 사용자 메시지로 주입 — 텍스트 응답 경로와 동일한 ReAct 루프.
        # 화이트리스트는 위에서 통과했으므로 ``handle_message`` 가 다시 한 번
        # 호출되지만 결과는 동일. 응답은 메시지의 ``reply_text`` (= 같은 chat) 으로 전송.
        if self._message_handler is None:
            return

        try:
            response = await self._message_handler(option.body, user_id, chat_id)
        except Exception:
            logger.exception("clarify callback message handler error")
            response = "An error occurred while processing your selection."

        if response is None:
            return

        # callback_query 의 ``message`` 는 봇이 보낸 원본 (질문 메시지) 이므로
        # ``reply_text`` 가 같은 chat 에 답변을 이어준다. 응답이 clarify 후속이면
        # 또 키보드가 붙는다.
        await self._send_response(query, response, chat_id=chat_id)

    async def start(self) -> None:
        """텔레그램 봇 폴링을 시작한다."""
        if not self._bot_token:
            logger.warning("Telegram bot token not configured. Skipping.")
            return

        try:
            from telegram.ext import (
                ApplicationBuilder,
                CallbackQueryHandler,
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
                    if response is None:
                        return
                    await self._send_response(
                        update, response, chat_id=chat_id,
                    )

            self._application.add_handler(
                MessageHandler(filters.TEXT, _on_message)
            )
            self._application.add_handler(
                CallbackQueryHandler(self._on_callback_query)
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
