"""텔레그램 봇: 폴링 기반 메시지 수신, 화이트리스트 인증, 메시지 핸들링.

python-telegram-bot 라이브러리를 사용하여 텔레그램 메시지를 수신·응답한다.
- 화이트리스트(user_id / chat_id) 기반 접근 제어 (fail-closed 정책)
- 모든 접근 시도를 AccessAttempt으로 기록
- 외부 message_handler 콜백을 주입받아 메시지 처리 위임
- BIZ-260: ReAct 에이전트의 ``clarify`` 도구가 호출되면 인라인 키보드 버튼으로
  옵션을 렌더 + ``callback_query`` 핸들러가 인바운드 메시지와 동일한 화이트리스트
  를 재검증한 뒤 선택지를 새 메시지로 주입.

스트리밍(BIZ-259):
  ``TelegramStreamSink`` 는 LLM 응답 텍스트 델타를 받아 editMessageText 로
  점진 갱신한다. rate-limit guard(``min_interval_ms`` + ``min_delta_chars``) 로
  텔레그램 API 한계를 회피하며, ``finalize`` 시 BIZ-253 분할 로직과 결합해
  4096자 한계도 자연 분할한다. 부분 마크다운 깨짐 회귀를 피하기 위해 스트림
  중에는 plain text 로만 edit 하고, finalize 단계는 호출 측이 결정한다(현재는 plain).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict
from typing import Awaitable, Callable

from simpleclaw.agent.progress import (
    ProgressCallback,
    ProgressEvent,
    format_progress_line,
)
from simpleclaw.agent.clarify import (
    ClarifyOption,
    ClarifyRequest,
    decode_callback_data,
    encode_callback_data,
)
from simpleclaw.channels.models import AccessAttempt
from simpleclaw.llm.models import MultimodalAttachment
from simpleclaw.proactive.presenter import (
    build_proactive_callback_data,
    parse_proactive_callback_data,
)

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


class TelegramStreamSink:
    """LLM 응답 델타를 텔레그램에 점진적으로 반영하는 sink (BIZ-259).

    수명주기:
        1. ``await sink.start()`` — placeholder 메시지를 보내고 ``message_id`` 를 잡는다.
        2. ``await sink.on_text_delta(delta)`` — 텍스트 조각을 받을 때마다 호출.
           내부 buffer 에 누적되며 rate-limit / min-delta guard 를 통과할 때만
           ``editMessageText`` 가 호출된다.
        3. ``await sink.finalize(final_text)`` — 최종 텍스트로 메시지를 교체한다.
           ``final_text`` 가 4096자를 넘으면 BIZ-253 ``split_for_telegram`` 으로
           나눠 첫 청크는 placeholder 를 edit 하고 후속 청크는 sendMessage 로 보낸다.

    설계 결정:
    - **plain text 전용 edit**: 스트림 중에는 ``parse_mode=None`` 으로 편집한다.
      Markdown 모드는 부분 텍스트의 펜스/굵게 짝이 안 맞으면 텔레그램이 BadRequest
      로 거부하기 때문(Hermes #22518 의 핵심 회피). finalize 도 plain text 로 보낸다.
    - **rate-limit guard**: 마지막 edit 후 ``min_interval_ms`` 미경과면 토큰만 buffer
      에 쌓고 호출을 미룬다. 누적 델타가 ``min_delta_chars`` 미만이어도 미룬다 —
      잦은 1~2자 갱신으로 인한 시각 흔들림 + 텔레그램 ``editMessageText`` 한계 회피.
    - **edit failure soft-fail**: 텔레그램 API 가 거부하면 (FloodWait / parse error)
      WARN 로그만 남기고 buffer 는 유지한다 — 다음 edit 시도 또는 finalize 에서
      flush 된다. 사용자에게 빈 메시지가 노출되지 않는다.
    - **idempotent**: 동일 텍스트로 두 번 연속 edit 호출하면 텔레그램이 거부하므로
      ``_last_committed_text`` 와 비교해 변화 없으면 skip 한다.

    호출 측은 ``bot`` 로 python-telegram-bot 의 ``telegram.Bot`` (또는 호환 mock) 을
    주입한다 — 테스트에서 fake bot 으로 검증 가능.
    """

    def __init__(
        self,
        bot,
        chat_id: int,
        *,
        min_interval_ms: int = 800,
        min_delta_chars: int = 40,
        initial_placeholder: str = "…",
        reply_to_message_id: int | None = None,
    ) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._min_interval_s = max(min_interval_ms, 0) / 1000.0
        self._min_delta_chars = max(min_delta_chars, 1)
        self._placeholder = initial_placeholder or "…"
        self._reply_to = reply_to_message_id

        self._message_id: int | None = None
        self._accumulated: str = ""
        self._last_committed_text: str = ""
        self._last_edit_ts: float = 0.0
        self._lock = asyncio.Lock()
        self._finalized = False
        self._progress_lines: list[str] = []

    @property
    def message_id(self) -> int | None:
        return self._message_id

    @property
    def accumulated_text(self) -> str:
        return self._accumulated

    async def start(self) -> None:
        """Placeholder 메시지를 보내 streaming 시작을 표시한다.

        실패하면 sink 는 비활성 상태로 남아 후속 ``on_text_delta`` 가 no-op 가 된다 —
        호출 측이 별도로 final 답을 보내야 한다. 이중 send 회피를 위해 finalize 도
        message_id 미설정 시 plain send 로 fallback.
        """
        try:
            msg = await self._bot.send_message(
                chat_id=self._chat_id,
                text=self._placeholder,
                reply_to_message_id=self._reply_to,
            )
            self._message_id = getattr(msg, "message_id", None)
            self._last_committed_text = self._placeholder
            self._last_edit_ts = time.monotonic()
        except Exception as exc:  # noqa: BLE001
            logger.warning("TelegramStreamSink.start failed: %s", exc)
            self._message_id = None

    async def on_text_delta(self, delta: str) -> None:
        """텍스트 델타를 누적하고 rate-limit / min-delta guard 통과 시 edit 호출."""
        if self._finalized or self._message_id is None or not delta:
            self._accumulated += delta or ""
            return

        async with self._lock:
            self._accumulated += delta
            pending = len(self._accumulated) - len(self._last_committed_text)
            if pending < self._min_delta_chars:
                return
            now = time.monotonic()
            if now - self._last_edit_ts < self._min_interval_s:
                return
            await self._commit_edit(self._render_stream_text())

    def _render_stream_text(self) -> str:
        """누적 텍스트와 최근 progress 이벤트를 placeholder 용 본문으로 합친다."""
        base = self._accumulated.strip()
        progress = "\n".join(self._progress_lines[-6:])
        if progress:
            body = f"{base}\n\n진행 상황\n{progress}" if base else f"진행 상황\n{progress}"
            return body
        return self._accumulated

    async def on_progress_event(self, event: ProgressEvent) -> None:
        """도구/레시피 progress 이벤트를 placeholder 메시지에 compact 하게 표시한다."""
        if self._finalized or self._message_id is None:
            return
        async with self._lock:
            self._progress_lines.append(format_progress_line(event))
            await self._commit_edit(self._render_stream_text())

    async def _commit_edit(self, text: str) -> None:
        """``editMessageText`` 호출. 동일 텍스트면 skip, 실패는 WARN."""
        if not text or text == self._last_committed_text:
            return
        # 텔레그램 4096 초과 텍스트는 edit 으로 보낼 수 없다 — 4090자에서 자르고
        # 마지막에 ellipsis 를 붙여 사용자에게 "...연결됨" 신호를 준다.
        # 실제 4096 한계 자연 분할은 finalize 에서 BIZ-253 로직으로 처리.
        if len(text) > TELEGRAM_MESSAGE_LIMIT:
            text = text[: TELEGRAM_MESSAGE_LIMIT - 1] + "…"
        try:
            await self._bot.edit_message_text(
                chat_id=self._chat_id,
                message_id=self._message_id,
                text=text,
            )
            self._last_committed_text = text
            self._last_edit_ts = time.monotonic()
        except Exception as exc:  # noqa: BLE001
            # FloodWait / parse error / message-not-modified 류는 다음 시도에서 회복.
            logger.warning(
                "TelegramStreamSink edit_message_text failed: %s", exc
            )

    async def finalize(self, final_text: str | None = None) -> list[str]:
        """최종 텍스트로 메시지를 교체한다. 4096 초과 시 BIZ-253 분할.

        Args:
            final_text: 최종 텍스트. None 이면 누적된 ``accumulated_text`` 사용 —
                fallback provider(스트리밍 미지원) 에서도 동일 경로로 동작.

        Returns:
            실제 텔레그램에 보내진 (또는 edit 된) 청크들의 텍스트 리스트.
        """
        if self._finalized:
            return []
        self._finalized = True

        text = final_text if final_text is not None else self._accumulated
        text = text or ""

        # 빈 응답: 사용자에게 placeholder 가 남는 사고를 막기 위해 명시적 안내로 교체.
        if not text.strip():
            text = "(빈 응답)"

        chunks = split_for_telegram(text)

        async with self._lock:
            sent_chunks: list[str] = []
            # 첫 청크: placeholder 가 살아있으면 editMessageText 로 교체. 없으면 sendMessage.
            first = chunks[0]
            if self._message_id is not None:
                try:
                    await self._bot.edit_message_text(
                        chat_id=self._chat_id,
                        message_id=self._message_id,
                        text=first,
                    )
                    sent_chunks.append(first)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "TelegramStreamSink finalize first-edit failed, "
                        "fallback to send_message: %s", exc,
                    )
                    try:
                        await self._bot.send_message(
                            chat_id=self._chat_id, text=first,
                        )
                        sent_chunks.append(first)
                    except Exception as exc2:  # noqa: BLE001
                        logger.error(
                            "TelegramStreamSink finalize send fallback failed: %s",
                            exc2,
                        )
            else:
                # placeholder 가 안 보내졌으면 fresh send.
                try:
                    await self._bot.send_message(
                        chat_id=self._chat_id, text=first,
                    )
                    sent_chunks.append(first)
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "TelegramStreamSink finalize send failed: %s", exc,
                    )

            for chunk in chunks[1:]:
                try:
                    await self._bot.send_message(
                        chat_id=self._chat_id, text=chunk,
                    )
                    sent_chunks.append(chunk)
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "TelegramStreamSink finalize chunk send failed: %s",
                        exc,
                    )

            self._last_committed_text = sent_chunks[-1] if sent_chunks else ""
            return sent_chunks


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
        message_handler: Callable[..., Awaitable[str]] | None = None,
        clarify_provider: Callable[[int], ClarifyRequest | None] | None = None,
        streaming_config: dict | None = None,
        proactive_callback_handler: Callable[..., Awaitable[str]] | None = None,
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
        # BIZ-259 — 스트리밍 설정. None 또는 enabled=False 면 기존 동작(완성 후 1회 send).
        # final_only_for_cron 는 본 인바운드 경로(/cron 명령 응답 외) 에서는 무관 —
        # CronScheduler 의 notifier 가 별도 경로라 인바운드 sink 와 격리된다.
        self._streaming_config = streaming_config or {}
        self._proactive_callback_handler = proactive_callback_handler

    def set_proactive_callback_handler(
        self, handler: Callable[..., Awaitable[str]] | None
    ) -> None:
        """proactive 제안 버튼 callback을 처리할 async handler를 연결한다."""
        self._proactive_callback_handler = handler

    async def send_proactive_opportunity(
        self, *, chat_id: int, opportunity, text: str | None = None
    ) -> None:
        """proactive 제안을 InlineKeyboard와 함께 Telegram으로 보낸다."""
        if self._application is None:
            raise RuntimeError("Telegram application is not started")
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        rows = [
            [
                InlineKeyboardButton(
                    "등록",
                    callback_data=build_proactive_callback_data(
                        "accept", opportunity.id
                    ),
                ),
                InlineKeyboardButton(
                    "시간 변경",
                    callback_data=build_proactive_callback_data("edit", opportunity.id),
                ),
            ],
            [
                InlineKeyboardButton(
                    "나중에",
                    callback_data=build_proactive_callback_data(
                        "snooze", opportunity.id
                    ),
                ),
                InlineKeyboardButton(
                    "아니요",
                    callback_data=build_proactive_callback_data(
                        "dismiss", opportunity.id
                    ),
                ),
            ],
        ]
        await self._application.bot.send_message(
            chat_id=chat_id,
            text=(text or opportunity.message_draft or opportunity.title)[
                :TELEGRAM_MESSAGE_LIMIT
            ],
            reply_markup=InlineKeyboardMarkup(rows),
        )

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
        self,
        text: str,
        user_id: int,
        chat_id: int,
        *,
        attachments: list[MultimodalAttachment] | None = None,
        on_text_delta: Callable[[str], Awaitable[None]] | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> str | None:
        """수신 메시지를 인증 후 처리한다.

        비인가 사용자이면 None을 반환하고, 핸들러가 없으면 에코 응답을 보낸다.

        BIZ-259: ``on_text_delta`` 가 주어지면 message_handler 에 kwarg 로 전달한다.
        오케스트레이터는 LLM 라우터의 ``stream()`` 경로로 전환되어 텍스트 델타를
        sink 콜백으로 흘려보낸다. BIZ-329: ``on_progress`` 는 실제 tool/recipe
        런타임 이벤트를 같은 placeholder 에 compact 하게 표시한다.
        """
        if not self.is_authorized(user_id, chat_id):
            self.log_access(user_id, chat_id, authorized=False)
            return None

        self.log_access(user_id, chat_id, authorized=True)

        # 텔레그램 메시지 최대 길이 제한 (4096자)
        text = text[:4096] if len(text) > 4096 else text

        if self._message_handler:
            try:
                kwargs = {}
                if attachments:
                    kwargs["attachments"] = attachments
                if on_text_delta is not None:
                    kwargs["on_text_delta"] = on_text_delta
                if on_progress is not None:
                    kwargs["on_progress"] = on_progress
                if kwargs:
                    return await self._message_handler(
                        text, user_id, chat_id, **kwargs,
                    )
                return await self._message_handler(text, user_id, chat_id)
            except TypeError as exc:
                # 기존 커스텀 handler 가 새 kwargs를 받지 못하는 경우 텍스트-only
                # 경로는 계속 살린다. attachment가 있는 요청에서 이 fallback이 발생하면
                # 이미지는 처리되지 않지만 봇 전체 응답은 깨지지 않는다.
                if attachments and "unexpected keyword" in str(exc):
                    logger.warning(
                        "Message handler does not accept attachments; falling back "
                        "to text-only call"
                    )
                    try:
                        return await self._message_handler(text, user_id, chat_id)
                    except Exception:
                        logger.exception("Message handler error")
                        return "An error occurred while processing your message."
                logger.exception("Message handler error")
                return "An error occurred while processing your message."
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

        proactive = parse_proactive_callback_data(data)
        if proactive is not None:
            action, opportunity_id = proactive
            if self._proactive_callback_handler is None:
                await query.answer(text="이 제안은 만료되었습니다.", show_alert=False)
                return
            try:
                response = await self._proactive_callback_handler(
                    action, opportunity_id
                )
            except Exception:  # noqa: BLE001 — callback UX 보호.
                logger.exception("proactive callback handler error")
                response = "제안 처리 중 오류가 발생했습니다."
            try:
                await query.answer(text=response[:200], show_alert=False)
            except Exception:
                logger.debug("callback_query.answer() failed (proactive)")
            if response:
                await query.message.reply_text(response[:TELEGRAM_MESSAGE_LIMIT])
            return

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

    @staticmethod
    async def _download_message_attachments(message, bot) -> list[MultimodalAttachment]:
        """Telegram 메시지의 이미지 첨부를 인증 후 LLM용 bytes로 다운로드한다.

        Telegram photo는 해상도별 후보가 오므로 가장 큰 항목(마지막)을 선택한다.
        document는 MIME type이 ``image/*``인 경우에만 인라인 이미지 데이터로 취급한다.
        파일 ID/URL은 provider에 넘기지 않고 이 지점에서 bytes로 닫아 Gemini 문서의
        ``types.Part.from_bytes`` 경로에 맞춘다.
        """
        attachments: list[MultimodalAttachment] = []

        photos = list(getattr(message, "photo", None) or [])
        if photos:
            largest = photos[-1]
            file_id = getattr(largest, "file_id", None)
            if file_id:
                try:
                    tg_file = await bot.get_file(file_id)
                    payload = await tg_file.download_as_bytearray()
                    attachments.append(
                        MultimodalAttachment(
                            data=bytes(payload),
                            mime_type="image/jpeg",
                            name=f"telegram-photo-{file_id}",
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Telegram photo download failed: %s", exc)

        document = getattr(message, "document", None)
        mime_type = getattr(document, "mime_type", "") if document is not None else ""
        if document is not None and str(mime_type).startswith("image/"):
            file_id = getattr(document, "file_id", None)
            if file_id:
                try:
                    tg_file = await bot.get_file(file_id)
                    payload = await tg_file.download_as_bytearray()
                    attachments.append(
                        MultimodalAttachment(
                            data=bytes(payload),
                            mime_type=str(mime_type),
                            name=getattr(document, "file_name", None),
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Telegram document image download failed: %s", exc)

        return attachments

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
                if update.message:
                    message_text = update.message.text or update.message.caption or ""
                    user_id = update.message.from_user.id
                    chat_id = update.message.chat_id
                    authorized = self.is_authorized(user_id, chat_id)
                    attachments = (
                        await self._download_message_attachments(update.message, context.bot)
                        if authorized
                        else []
                    )
                    if attachments and not message_text.strip():
                        message_text = "이미지를 분석해 주세요."
                    if not message_text and not attachments:
                        return

                    # BIZ-259 — streaming.enabled 일 때 인증 후 sink 생성.
                    # 인증 실패는 handle_message 가 None 을 반환하므로 sink 누설 없음
                    # (먼저 인증 체크).
                    streaming_enabled = bool(
                        self._streaming_config.get("enabled", False)
                    )
                    if streaming_enabled and authorized:
                        sink = TelegramStreamSink(
                            bot=context.bot,
                            chat_id=chat_id,
                            min_interval_ms=int(
                                self._streaming_config.get("min_interval_ms", 800)
                            ),
                            min_delta_chars=int(
                                self._streaming_config.get("min_delta_chars", 40)
                            ),
                            initial_placeholder=str(
                                self._streaming_config.get(
                                    "initial_placeholder", "…"
                                )
                            ),
                            reply_to_message_id=update.message.message_id,
                        )
                        await sink.start()
                        response = await self.handle_message(
                            message_text, user_id, chat_id,
                            attachments=attachments,
                            on_text_delta=sink.on_text_delta,
                            on_progress=(
                                sink.on_progress_event
                                if bool(self._streaming_config.get("tool_progress", True))
                                else None
                            ),
                        )
                        # BIZ-260 + BIZ-259 통합 — clarify 가 pending 이면 sink 의
                        # placeholder/스트리밍 결과를 버리고 인라인 키보드 경로로 전환.
                        # 비스트리밍 경로와 동일한 ``_send_response`` 흐름을 재사용한다.
                        # placeholder 메시지는 best-effort 로 지운다(rate-limit 거부 등은 흡수).
                        pending_request = None
                        if self._clarify_provider is not None:
                            try:
                                pending_request = self._clarify_provider(chat_id)
                            except Exception:
                                logger.exception(
                                    "clarify_provider raised in streaming path; "
                                    "falling back to text"
                                )
                        if pending_request is not None:
                            if sink.message_id is not None:
                                try:
                                    await context.bot.delete_message(
                                        chat_id=chat_id,
                                        message_id=sink.message_id,
                                    )
                                except Exception:  # noqa: BLE001
                                    pass
                            keyboard = self._build_inline_keyboard(pending_request)
                            sent = await update.message.reply_text(
                                pending_request.question[:TELEGRAM_MESSAGE_LIMIT],
                                reply_markup=keyboard,
                            )
                            self._cache_clarify_options(
                                chat_id, sent.message_id, pending_request.options,
                            )
                            logger.info(
                                "Telegram clarify sent (streaming path): chat=%d "
                                "msg=%d options=%d",
                                chat_id, sent.message_id, len(pending_request.options),
                            )
                            return
                        if response is None:
                            # 인증은 통과했지만 핸들러가 None 응답을 낸 경우 (예: future 분기).
                            # placeholder 가 남지 않도록 finalize 로 닫는다.
                            await sink.finalize("")
                            return
                        await sink.finalize(response)
                        return

                    # 비스트리밍 경로 — 기존 동작 유지(완성 후 BIZ-253 분할 전송).
                    response = await self.handle_message(
                        message_text, user_id, chat_id, attachments=attachments
                    )
                    if response is None:
                        return
                    await self._send_response(
                        update, response, chat_id=chat_id,
                    )

            self._application.add_handler(
                MessageHandler(filters.TEXT | filters.PHOTO | filters.Document.IMAGE, _on_message)
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
