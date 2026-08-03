"""OpenAI-compatible endpoint(OpenRouter) provider 설정 테스트 (BIZ-448/450).

`base_url` / `default_headers` 가 AsyncOpenAI 클라이언트 생성에 전달되고,
`extra_body` 가 send()/stream() Chat Completions 요청에 주입되는지 검증한다.
BIZ-450 — structured output 힌트가 `response_format` 으로 매핑되는지,
required 계약 위반 시 API 호출 전에 실패하는지도 검증한다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.agent.turn_plan import UNIFIED_TURN_PLAN_RESPONSE_SCHEMA
from simpleclaw.llm.models import LLMProviderError, MultimodalAttachment
from simpleclaw.llm.profiles import get_provider_profile
from simpleclaw.llm.providers.openai_provider import OpenAIProvider


def _contains_key(node: object, key: str) -> bool:
    if isinstance(node, dict):
        return key in node or any(_contains_key(value, key) for value in node.values())
    if isinstance(node, list):
        return any(_contains_key(item, key) for item in node)
    return False


def _multimodal_provider(monkeypatch) -> OpenAIProvider:
    monkeypatch.setattr(
        "simpleclaw.llm.providers.openai_provider.openai.AsyncOpenAI",
        lambda **_: MagicMock(),
    )
    return OpenAIProvider(
        model="google/gemini-3.6-flash",
        api_key="test-key",
        name="openrouter_gemini_3_6_flash",
        profile=get_provider_profile("openrouter-multimodal"),
    )


def test_openrouter_multimodal_maps_image_after_text(monkeypatch):
    provider = _multimodal_provider(monkeypatch)

    converted = provider._convert_messages(
        [
            {
                "role": "user",
                "content": "Describe the image.",
                "attachments": [
                    MultimodalAttachment(data=b"\x89PNG", mime_type="image/png")
                ],
            }
        ]
    )

    assert converted == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe the image."},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,iVBORw=="},
                },
            ],
        }
    ]


def test_openrouter_multimodal_maps_documents_and_filename_fallback(monkeypatch):
    provider = _multimodal_provider(monkeypatch)

    converted = provider._convert_messages(
        [
            {
                "role": "user",
                "content": "Read both files.",
                "attachments": [
                    MultimodalAttachment(
                        data=b"%PDF", mime_type="application/pdf", name="brief.pdf"
                    ),
                    MultimodalAttachment(data=b"notes", mime_type="text/plain"),
                ],
            }
        ]
    )

    parts = converted[0]["content"]
    assert parts[1] == {
        "type": "file",
        "file": {
            "filename": "brief.pdf",
            "file_data": "data:application/pdf;base64,JVBERg==",
        },
    }
    assert parts[2] == {
        "type": "file",
        "file": {
            "filename": "attachment.txt",
            "file_data": "data:text/plain;base64,bm90ZXM=",
        },
    }


def test_openrouter_multimodal_preserves_mixed_attachment_order(monkeypatch):
    provider = _multimodal_provider(monkeypatch)

    converted = provider._convert_messages(
        [
            {
                "role": "user",
                "content": "Inspect these in order.",
                "attachments": [
                    MultimodalAttachment(data=b"image", mime_type="image/jpeg"),
                    MultimodalAttachment(data=b"doc", mime_type="text/markdown"),
                    MultimodalAttachment(data=b"more", mime_type="image/webp"),
                ],
            }
        ]
    )

    assert [part["type"] for part in converted[0]["content"]] == [
        "text",
        "image_url",
        "file",
        "image_url",
    ]


@pytest.mark.parametrize(
    ("attachment", "match"),
    [
        (
            MultimodalAttachment(data=b"", mime_type="image/png"),
            "must not be empty",
        ),
        ({"data": b"payload"}, "MIME type is required"),
        (
            MultimodalAttachment(data=b"payload", mime_type="application/octet-stream"),
            "does not support attachment MIME type",
        ),
    ],
)
def test_openrouter_multimodal_rejects_invalid_attachments(
    monkeypatch, attachment, match
):
    provider = _multimodal_provider(monkeypatch)

    with pytest.raises(LLMProviderError, match=match):
        provider._convert_messages(
            [{"role": "user", "content": "Inspect.", "attachments": [attachment]}]
        )


def test_openrouter_multimodal_keeps_plain_message_payloads_unchanged(monkeypatch):
    provider = _multimodal_provider(monkeypatch)
    source = [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "name": "lookup", "arguments": {"q": "x"}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "result"},
    ]

    assert provider._convert_messages(source) == [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": '{"q": "x"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "result"},
    ]


@pytest.mark.asyncio
async def test_openrouter_multimodal_send_and_stream_share_attachment_mapping(
    monkeypatch,
):
    send_response = MagicMock(
        choices=[MagicMock(message=MagicMock(content="ok", tool_calls=None))],
        usage=None,
    )

    class _Stream:
        def __aiter__(self):
            async def gen():
                chunk = MagicMock()
                chunk.usage = None
                chunk.choices = []
                yield chunk

            return gen()

    create = AsyncMock(side_effect=[send_response, _Stream()])
    client = MagicMock()
    client.chat.completions.create = create
    monkeypatch.setattr(
        "simpleclaw.llm.providers.openai_provider.openai.AsyncOpenAI",
        lambda **_: client,
    )
    provider = OpenAIProvider(
        model="google/gemini-3.6-flash",
        api_key="test-key",
        profile=get_provider_profile("openrouter-multimodal"),
    )
    messages = [
        {
            "role": "user",
            "content": "Inspect.",
            "attachments": [MultimodalAttachment(data=b"image", mime_type="image/png")],
        }
    ]

    await provider.send(system_prompt="", user_message="", messages=messages)
    await provider.stream(system_prompt="", user_message="", messages=messages)

    send_messages = create.await_args_list[0].kwargs["messages"]
    stream_messages = create.await_args_list[1].kwargs["messages"]
    assert send_messages == stream_messages
    assert send_messages[0]["content"][1]["type"] == "image_url"


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
        "effort": "medium",
    }


@pytest.mark.asyncio
async def test_openrouter_profile_maps_reasoning_budget(monkeypatch):
    """provider-neutral budget_tokens를 OpenRouter max_tokens로 변환한다."""
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
        reasoning={"enabled": True, "effort": "medium", "budget_tokens": 512},
    )

    assert create.call_args.kwargs["extra_body"] == {"reasoning": {"max_tokens": 512}}


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
            choices=[
                MagicMock(message=MagicMock(content='{"ok":true}', tool_calls=None))
            ],
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
    assert (
        kwargs["response_format"]["json_schema"]["name"]
        == "simpleclaw_structured_response"
    )
    assert kwargs["response_format"]["json_schema"]["strict"] is True
    outgoing_schema = kwargs["response_format"]["json_schema"]["schema"]
    assert outgoing_schema["type"] == "object"
    assert "propertyOrdering" not in outgoing_schema
    assert kwargs["extra_body"] == {"reasoning": {"enabled": False}}


@pytest.mark.asyncio
async def test_openrouter_send_adapts_unified_planner_schema_and_keeps_strict(monkeypatch):
    create = AsyncMock(
        return_value=MagicMock(
            choices=[
                MagicMock(message=MagicMock(content="{}", tool_calls=None))
            ],
            usage=MagicMock(prompt_tokens=5, completion_tokens=1),
        )
    )
    client = MagicMock()
    client.chat.completions.create = create
    monkeypatch.setattr(
        "simpleclaw.llm.providers.openai_provider.openai.AsyncOpenAI",
        lambda **_: client,
    )

    provider = OpenAIProvider(
        model="google/gemini-3.6-flash",
        api_key="test-key",
        name="openrouter_gemini_3_6_flash",
        base_url="https://openrouter.ai/api/v1",
        profile=get_provider_profile("openrouter-multimodal"),
    )

    await provider.send(
        system_prompt="",
        user_message="plan",
        response_mime_type="application/json",
        response_schema=UNIFIED_TURN_PLAN_RESPONSE_SCHEMA,
        require_structured_output=True,
    )

    response_format = create.call_args.kwargs["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    outgoing_schema = response_format["json_schema"]["schema"]
    assert not _contains_key(outgoing_schema, "propertyOrdering")
    assert not _contains_key(outgoing_schema, "maxItems")
    assert outgoing_schema["properties"]["fact_check"]["required"] == [
        "required",
        "owner",
        "domain",
        "intents",
        "entities",
        "reference_date",
        "search_query",
        "required_claims",
        "freshness_required",
        "reason",
    ]


@pytest.mark.asyncio
async def test_openai_provider_send_maps_optional_json_mime_to_json_object(monkeypatch):
    create = AsyncMock(
        return_value=MagicMock(
            choices=[
                MagicMock(message=MagicMock(content='{"ok":true}', tool_calls=None))
            ],
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
