"""OpenAI-compatible endpoint(OpenRouter) provider 설정 테스트 (BIZ-448/450).

`base_url` / `default_headers` 가 AsyncOpenAI 클라이언트 생성에 전달되고,
`extra_body` 가 send()/stream() Chat Completions 요청에 주입되는지 검증한다.
BIZ-450 — structured output 힌트가 `response_format` 으로 매핑되는지,
required 계약 위반 시 API 호출 전에 실패하는지도 검증한다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.llm.models import LLMProviderError
from simpleclaw.llm.providers.openai_provider import OpenAIProvider
from simpleclaw.llm.profiles import get_provider_profile


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
async def test_openrouter_profile_maps_reasoning_hint(monkeypatch):
    create = AsyncMock(
        return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content="ok", tool_calls=None))],
            usage=None,
        )
    )
    client = MagicMock()
    client.chat.completions.create = create
    monkeypatch.setattr(
        "simpleclaw.llm.providers.openai_provider.openai.AsyncOpenAI",
        lambda **_: client,
    )
    provider = OpenAIProvider(
        model="vendor/model",
        api_key="test-key",
        profile=get_provider_profile("openrouter"),
    )

    await provider.send(
        system_prompt="",
        user_message="hi",
        reasoning={"enabled": True, "effort": "medium"},
    )

    assert create.call_args.kwargs["extra_body"]["reasoning"] == {
        "enabled": True,
        "effort": "medium",
    }


@pytest.mark.asyncio
async def test_plain_openai_profile_does_not_emit_openrouter_reasoning(monkeypatch):
    create = AsyncMock(
        return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content="ok", tool_calls=None))],
            usage=None,
        )
    )
    client = MagicMock()
    client.chat.completions.create = create
    monkeypatch.setattr(
        "simpleclaw.llm.providers.openai_provider.openai.AsyncOpenAI",
        lambda **_: client,
    )
    provider = OpenAIProvider(model="gpt", api_key="test-key")

    await provider.send(
        system_prompt="",
        user_message="hi",
        reasoning={"enabled": True, "effort": "medium"},
    )

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


@pytest.mark.asyncio
async def test_openai_provider_send_maps_required_schema_to_json_schema(monkeypatch):
    create = AsyncMock(
        return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"ok":true}', tool_calls=None))],
            usage=MagicMock(prompt_tokens=5, completion_tokens=3),
        )
    )
    client = MagicMock()
    client.chat.completions.create = create
    monkeypatch.setattr(
        "simpleclaw.llm.providers.openai_provider.openai.AsyncOpenAI",
        lambda **_: client,
    )

    provider = OpenAIProvider(
        model="deepseek/deepseek-v4-pro",
        api_key="test-key",
        name="openrouter_deepseek_v4_pro",
        base_url="https://openrouter.ai/api/v1",
        extra_body={"reasoning": {"enabled": False}},
    )
    schema = {
        "type": "object",
        "propertyOrdering": ["ok"],
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }

    response = await provider.send(
        system_prompt="",
        user_message="json",
        response_mime_type="application/json",
        response_schema=schema,
        require_structured_output=True,
    )

    assert response.text == '{"ok":true}'
    kwargs = create.call_args.kwargs
    assert kwargs["response_format"]["type"] == "json_schema"
    assert kwargs["response_format"]["json_schema"]["name"] == "simpleclaw_structured_response"
    assert kwargs["response_format"]["json_schema"]["strict"] is True
    outgoing_schema = kwargs["response_format"]["json_schema"]["schema"]
    assert outgoing_schema["type"] == "object"
    assert "propertyOrdering" not in outgoing_schema
    assert kwargs["extra_body"] == {"reasoning": {"enabled": False}}


@pytest.mark.asyncio
async def test_openai_provider_send_maps_optional_json_mime_to_json_object(monkeypatch):
    create = AsyncMock(
        return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"ok":true}', tool_calls=None))],
            usage=MagicMock(prompt_tokens=5, completion_tokens=3),
        )
    )
    client = MagicMock()
    client.chat.completions.create = create
    monkeypatch.setattr(
        "simpleclaw.llm.providers.openai_provider.openai.AsyncOpenAI",
        lambda **_: client,
    )

    provider = OpenAIProvider(model="gpt-4o", api_key="test-key")

    await provider.send(
        system_prompt="",
        user_message="json",
        response_mime_type="application/json",
        response_schema=None,
        require_structured_output=False,
    )

    assert create.call_args.kwargs["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_openai_provider_send_omits_response_format_without_hints(monkeypatch):
    """structured 힌트가 전혀 없으면 요청 kwargs 에 response_format 키 자체가 없어야 함."""
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

    assert "response_format" not in create.call_args.kwargs


@pytest.mark.asyncio
async def test_openai_provider_required_structured_output_requires_schema(monkeypatch):
    create = AsyncMock()
    client = MagicMock()
    client.chat.completions.create = create
    monkeypatch.setattr(
        "simpleclaw.llm.providers.openai_provider.openai.AsyncOpenAI",
        lambda **_: client,
    )

    provider = OpenAIProvider(model="gpt-4o", api_key="test-key")

    with pytest.raises(LLMProviderError, match="requires response_schema"):
        await provider.send(
            system_prompt="",
            user_message="json",
            response_mime_type="application/json",
            response_schema=None,
            require_structured_output=True,
        )

    create.assert_not_called()


@pytest.mark.asyncio
async def test_openai_provider_rejects_non_dict_schema(monkeypatch):
    """dict JSON Schema 외 타입(예: Pydantic class)은 API 호출 전에 거부한다."""
    create = AsyncMock()
    client = MagicMock()
    client.chat.completions.create = create
    monkeypatch.setattr(
        "simpleclaw.llm.providers.openai_provider.openai.AsyncOpenAI",
        lambda **_: client,
    )

    provider = OpenAIProvider(model="gpt-4o", api_key="test-key")

    with pytest.raises(LLMProviderError, match="dict JSON Schema"):
        await provider.send(
            system_prompt="",
            user_message="json",
            response_mime_type="application/json",
            response_schema=str,
            require_structured_output=True,
        )

    create.assert_not_called()


@pytest.mark.asyncio
async def test_openai_provider_stream_includes_response_format(monkeypatch):
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

    provider = OpenAIProvider(model="deepseek/deepseek-v4-pro", api_key="test-key")
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }

    await provider.stream(
        system_prompt="",
        user_message="json",
        response_mime_type="application/json",
        response_schema=schema,
        require_structured_output=True,
    )

    assert create.call_args.kwargs["response_format"]["type"] == "json_schema"


@pytest.mark.asyncio
async def test_openai_provider_ignores_reasoning_hint(monkeypatch):
    """BIZ-453 — provider-neutral reasoning hint 는 무시되고, config 의
    extra_body.reasoning 정책을 덮어쓰지 않는다."""
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

    response = await provider.send(
        system_prompt="",
        user_message="hi",
        reasoning={"enabled": True, "effort": "medium", "budget_tokens": 512},
    )

    assert response.text == "ok"
    # hint 는 요청 필드로 매핑되지 않고, 정적 extra_body 정책이 그대로 유지된다.
    assert "reasoning" not in create.call_args.kwargs
    assert create.call_args.kwargs["extra_body"] == {"reasoning": {"enabled": False}}
