"""OpenAI-compatible endpoint(OpenRouter) provider 설정 테스트 (BIZ-448).

`base_url` / `default_headers` 가 AsyncOpenAI 클라이언트 생성에 전달되고,
`extra_body` 가 send()/stream() Chat Completions 요청에 주입되는지 검증한다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.llm.providers.openai_provider import OpenAIProvider


def test_openai_provider_passes_base_url_and_default_headers(monkeypatch):
    captured: dict = {}

    def fake_async_openai(**kwargs):
        captured.update(kwargs)
        client = MagicMock()
        client.chat.completions.create = MagicMock()
        return client

    monkeypatch.setattr(
        "simpleclaw.llm.providers.openai_provider.openai.AsyncOpenAI",
        fake_async_openai,
    )

    OpenAIProvider(
        model="z-ai/glm-5.2",
        api_key="test-key",
        name="openrouter_glm_5_2",
        base_url="https://openrouter.ai/api/v1",
        default_headers={
            "HTTP-Referer": "https://simpleclaw.local",
            "X-Title": "SimpleClaw",
        },
    )

    assert captured["api_key"] == "test-key"
    assert captured["base_url"] == "https://openrouter.ai/api/v1"
    assert captured["default_headers"] == {
        "HTTP-Referer": "https://simpleclaw.local",
        "X-Title": "SimpleClaw",
    }


def test_openai_provider_defaults_keep_plain_openai_client(monkeypatch):
    """base_url/default_headers 미지정 시 None 으로 전달 — 기존 OpenAI 동작 유지."""
    captured: dict = {}

    def fake_async_openai(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(
        "simpleclaw.llm.providers.openai_provider.openai.AsyncOpenAI",
        fake_async_openai,
    )

    OpenAIProvider(model="gpt-4o", api_key="test-key")

    assert captured["api_key"] == "test-key"
    assert captured["base_url"] is None
    assert captured["default_headers"] is None


@pytest.mark.asyncio
async def test_openai_provider_send_includes_extra_body(monkeypatch):
    create = AsyncMock(
        return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content="ok", tool_calls=None))],
            usage=MagicMock(prompt_tokens=3, completion_tokens=1),
        )
    )
    client = MagicMock()
    client.chat.completions.create = create
    monkeypatch.setattr(
        "simpleclaw.llm.providers.openai_provider.openai.AsyncOpenAI",
        lambda **_: client,
    )

    provider = OpenAIProvider(
        model="z-ai/glm-5.2",
        api_key="test-key",
        name="openrouter_glm_5_2",
        extra_body={"reasoning": {"enabled": False}},
    )

    response = await provider.send(system_prompt="", user_message="hi")

    assert response.text == "ok"
    assert create.call_args.kwargs["extra_body"] == {"reasoning": {"enabled": False}}


@pytest.mark.asyncio
async def test_openai_provider_send_omits_extra_body_when_unset(monkeypatch):
    """extra_body 미설정 시 요청 kwargs 에 키 자체가 없어야 함 — 회귀 0."""
    create = AsyncMock(
        return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content="ok", tool_calls=None))],
            usage=MagicMock(prompt_tokens=3, completion_tokens=1),
        )
    )
    client = MagicMock()
    client.chat.completions.create = create
    monkeypatch.setattr(
        "simpleclaw.llm.providers.openai_provider.openai.AsyncOpenAI",
        lambda **_: client,
    )

    provider = OpenAIProvider(model="gpt-4o", api_key="test-key")

    await provider.send(system_prompt="", user_message="hi")

    assert "extra_body" not in create.call_args.kwargs


@pytest.mark.asyncio
async def test_openai_provider_stream_includes_extra_body(monkeypatch):
    def _chunk(text: str):
        delta = MagicMock()
        delta.content = text
        delta.tool_calls = None
        choice = MagicMock()
        choice.delta = delta
        chunk = MagicMock()
        chunk.choices = [choice]
        chunk.usage = None
        return chunk

    class _Iter:
        def __aiter__(self):
            async def gen():
                yield _chunk("ok")
            return gen()

    create = AsyncMock(return_value=_Iter())
    client = MagicMock()
    client.chat.completions.create = create
    monkeypatch.setattr(
        "simpleclaw.llm.providers.openai_provider.openai.AsyncOpenAI",
        lambda **_: client,
    )

    provider = OpenAIProvider(
        model="z-ai/glm-5.2",
        api_key="test-key",
        name="openrouter_glm_5_2",
        extra_body={"reasoning": {"enabled": False}},
    )

    response = await provider.stream(system_prompt="", user_message="hi")

    assert response.text == "ok"
    assert create.call_args.kwargs["extra_body"] == {"reasoning": {"enabled": False}}
