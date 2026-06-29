"""BIZ-397: cron Telegram notifier 긴 응답 분할 전송 회귀 테스트.

`krstock-close` 류 cron 결과가 4,096자를 넘을 때 ``text[:4096]`` 으로 절단되어
본문 뒷부분이 유실되던 장애를 재발 방지한다. 일반 대화 경로와 동일한
``split_for_telegram`` 분할 정책이 cron notifier 에도 적용되는지 검증한다.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from simpleclaw.channels.telegram_bot import TELEGRAM_MESSAGE_LIMIT


RUN_BOT_PATH = Path(__file__).parents[2] / "scripts" / "run_bot.py"


def _load_run_bot_module():
    """scripts/run_bot.py를 모듈로 로드한다.

    run_bot.py는 패키지 모듈이 아닌 launch script이므로 importlib spec로 직접 로드해
    helper 함수만 검증한다. ``if __name__ == "__main__"`` guard 덕분에 main은 실행되지 않는다.
    """
    spec = importlib.util.spec_from_file_location("run_bot_biz397", RUN_BOT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _RecordingBot:
    """send_message 호출 본문을 기록하는 텔레그램 Bot 더블."""

    sent: list[str] = []

    def __init__(self, token: str) -> None:
        self.token = token

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def send_message(self, chat_id: int, text: str) -> None:
        type(self).sent.append(text)


@pytest.fixture
def recording_telegram(monkeypatch):
    _RecordingBot.sent = []
    fake_telegram = types.SimpleNamespace(Bot=_RecordingBot)
    monkeypatch.setitem(sys.modules, "telegram", fake_telegram)
    return _RecordingBot


@pytest.mark.asyncio
async def test_cron_notifier_splits_long_messages_without_truncation(
    recording_telegram,
):
    run_bot = _load_run_bot_module()
    # 8,000자 입력 — 단일 4,096자 한계를 명백히 초과.
    text = "".join(f"line-{i:04d} 시장 브리핑 본문 내용\n" for i in range(360))
    assert len(text) > 8000

    notifier = run_bot._create_telegram_notifier("fake-token", 123)
    await notifier("krstock-close", text)

    sent = recording_telegram.sent
    # 여러 메시지로 분할 전송되어야 한다.
    assert len(sent) >= 2
    # 각 청크는 텔레그램 4,096자 한계를 넘지 않는다.
    assert all(len(chunk) <= TELEGRAM_MESSAGE_LIMIT for chunk in sent)
    # 분할 헤더 ``(i/N)\n`` 를 제거한 본문 합산이 원문 콘텐츠를 보존한다.
    # split_for_telegram 은 청크 경계의 개행만 정리하므로(콘텐츠 손실 없음),
    # 개행을 제거한 본문이 원문과 일치하면 절단이 없었음을 보장한다.
    bodies = [chunk.split("\n", 1)[1] for chunk in sent]
    assert "".join(bodies).replace("\n", "") == text.replace("\n", "")


@pytest.mark.asyncio
async def test_cron_notifier_sends_short_message_once(recording_telegram):
    run_bot = _load_run_bot_module()
    text = "짧은 cron 결과 요약."

    notifier = run_bot._create_telegram_notifier("fake-token", 123)
    await notifier("krstock-close", text)

    sent = recording_telegram.sent
    # 4,096자 이하 입력은 헤더 없이 단일 메시지로 전송된다.
    assert sent == [text]
