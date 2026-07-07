"""Tests for the LLM router."""

from unittest.mock import AsyncMock

import pytest

from simpleclaw.llm.models import LLMConfigError, LLMRequest, LLMResponse, SystemBlock
from simpleclaw.llm.providers.base import LLMProvider
from simpleclaw.llm.router import LLMRouter


class MockProvider(LLMProvider):
    """A mock provider for testing."""

    def __init__(self, name: str = "mock"):
        self._name = name
        self._mock_send = AsyncMock(
            return_value=LLMResponse(
                text=f"Response from {name}",
                backend_name=name,
                model="mock-model",
            )
        )

    async def send(
        self,
        system_prompt: str,
        user_message: str,
        messages: list[dict] | None = None,
        tools=None,
        system_blocks=None,
        max_tokens: int | None = None,
        **kwargs,  # BIZ-427 structured output 등 신규 optional kwargs 허용
    ) -> LLMResponse:
        return await self._mock_send(
            system_prompt, user_message, messages, tools, system_blocks, max_tokens
        )


class TestLLMRouter:
    @pytest.fixture
    def router(self):
        providers = {
            "provider_a": MockProvider("provider_a"),
            "provider_b": MockProvider("provider_b"),
        }
        return LLMRouter(
            backends={},
            providers=providers,
            default_backend="provider_a",
        )

    @pytest.mark.asyncio
    async def test_default_backend(self, router):
        request = LLMRequest(user_message="hello")
        response = await router.send(request)
        assert response.backend_name == "provider_a"

    @pytest.mark.asyncio
    async def test_explicit_backend(self, router):
        request = LLMRequest(user_message="hello", backend_name="provider_b")
        response = await router.send(request)
        assert response.backend_name == "provider_b"

    @pytest.mark.asyncio
    async def test_unknown_backend_raises(self, router):
        request = LLMRequest(user_message="hello", backend_name="nonexistent")
        with pytest.raises(LLMConfigError, match="Unknown backend"):
            await router.send(request)

    def test_list_backends(self, router):
        backends = router.list_backends()
        assert "provider_a" in backends
        assert "provider_b" in backends
        assert len(backends) == 2

    def test_get_default_backend(self, router):
        assert router.get_default_backend() == "provider_a"

    @pytest.mark.asyncio
    async def test_system_prompt_passed(self, router):
        request = LLMRequest(
            system_prompt="You are helpful.",
            user_message="hello",
        )
        await router.send(request)
        router._providers["provider_a"]._mock_send.assert_called_once_with(
            "You are helpful.", "hello", None, None, None, None
        )

    @pytest.mark.asyncio
    async def test_system_blocks_passed_through(self, router):
        """BIZ-252 — LLMRequest.system_blocks 가 프로바이더 send() 까지 전달되어야
        Anthropic prompt caching 마커가 부착될 수 있다."""
        blocks = [SystemBlock(text="persona", cache=True)]
        request = LLMRequest(
            system_prompt="legacy fallback",
            user_message="hello",
            system_blocks=blocks,
        )
        await router.send(request)
        router._providers["provider_a"]._mock_send.assert_called_once_with(
            "legacy fallback", "hello", None, None, blocks, None
        )

    @pytest.mark.asyncio
    async def test_send_without_callback_does_not_invoke_stream(self, router):
        """BIZ-259 — on_text_delta 미지정 시 send() 경로 유지 (회귀 0)."""
        provider = router._providers["provider_a"]
        # MockProvider 에는 stream() 오버라이드가 없으므로 호출 여부 확인을 위해 spy 부착.
        provider._mock_stream_called = False
        original_stream = provider.stream

        async def spy_stream(*args, **kwargs):
            provider._mock_stream_called = True
            return await original_stream(*args, **kwargs)

        provider.stream = spy_stream  # type: ignore[assignment]
        request = LLMRequest(user_message="hi")
        await router.send(request)
        assert provider._mock_stream_called is False

    @pytest.mark.asyncio
    async def test_send_with_callback_routes_to_provider_stream(self, router):
        """BIZ-259 — on_text_delta 지정 시 provider.stream() 으로 라우팅."""
        collected: list[str] = []

        async def cb(delta: str) -> None:
            collected.append(delta)

        # MockProvider 의 send() 가 "Response from provider_a" 텍스트를 돌려주므로
        # base 의 fallback stream() 이 그대로 콜백으로 흘려보낸다.
        request = LLMRequest(user_message="hi")
        await router.send(request, on_text_delta=cb)
        assert collected == ["Response from provider_a"]

    @pytest.mark.asyncio
    async def test_send_with_callback_routes_to_gemini_stream(self):
        """BIZ-284 — ``backend=gemini`` + on_text_delta 시 GeminiProvider.stream() 으로 라우팅.

        base 의 fallback (send 결과를 한 번에 콜백) 이 아니라 실제 stream() override 가
        호출되어 청크별 델타가 그대로 흘러야 한다 — Claude 와 동일 패턴.
        """
        from unittest.mock import AsyncMock, MagicMock

        from simpleclaw.llm.providers.gemini import GeminiProvider

        provider = GeminiProvider(model="gemini-2.0-flash", api_key="test-key")

        def _text_part(text: str) -> MagicMock:
            part = MagicMock()
            part.function_call = None
            part.text = text
            return part

        def _chunk(parts, usage=None):
            chunk = MagicMock()
            content = MagicMock()
            content.parts = parts
            candidate = MagicMock()
            candidate.content = content
            chunk.candidates = [candidate] if parts is not None else []
            chunk.usage_metadata = usage
            return chunk

        chunks = [
            _chunk([_text_part("Hello")]),
            _chunk([_text_part(" Gemini")]),
            _chunk(
                None,
                usage=MagicMock(prompt_token_count=4, candidates_token_count=2),
            ),
        ]

        class _Iter:
            def __init__(self, items):
                self._items = list(items)

            def __aiter__(self):
                async def gen():
                    for c in self._items:
                        yield c
                return gen()

        provider._client.aio.models.generate_content_stream = AsyncMock(
            return_value=_Iter(chunks)
        )

        router = LLMRouter(
            backends={},
            providers={"gemini": provider},
            default_backend="gemini",
        )

        collected: list[str] = []

        async def cb(delta: str) -> None:
            collected.append(delta)

        request = LLMRequest(user_message="hi", backend_name="gemini")
        result = await router.send(request, on_text_delta=cb)

        # 청크 단위 델타가 그대로 콜백에 흘러야 한다 — fallback 이면 ["Hello Gemini"] 한 덩이.
        assert collected == ["Hello", " Gemini"]
        assert result.text == "Hello Gemini"
        assert result.backend_name == "gemini"
        assert result.usage == {"input_tokens": 4, "output_tokens": 2}

    @pytest.mark.asyncio
    async def test_send_with_callback_routes_to_openai_stream(self):
        """BIZ-290 — ``backend=openai`` + on_text_delta 시 OpenAIProvider.stream() 으로 라우팅.

        base 의 fallback (send 결과를 한 번에 콜백) 이 아니라 실제 stream() override 가
        호출되어 청크별 델타가 그대로 흘러야 한다 — Claude/Gemini 와 동일 패턴.
        """
        from unittest.mock import AsyncMock, MagicMock

        from simpleclaw.llm.providers.openai_provider import OpenAIProvider

        provider = OpenAIProvider(model="gpt-4o", api_key="test-key")

        def _text_chunk(text: str) -> MagicMock:
            delta = MagicMock()
            delta.content = text
            delta.tool_calls = None
            choice = MagicMock()
            choice.delta = delta
            chunk = MagicMock()
            chunk.choices = [choice]
            chunk.usage = None
            return chunk

        def _usage_chunk(p: int, c: int) -> MagicMock:
            chunk = MagicMock()
            chunk.choices = []
            chunk.usage = MagicMock(prompt_tokens=p, completion_tokens=c)
            return chunk

        chunks = [
            _text_chunk("Hello"),
            _text_chunk(" OpenAI"),
            _usage_chunk(4, 2),
        ]

        class _Iter:
            def __init__(self, items):
                self._items = list(items)

            def __aiter__(self):
                async def gen():
                    for c in self._items:
                        yield c
                return gen()

        provider._client.chat.completions.create = AsyncMock(
            return_value=_Iter(chunks)
        )

        router = LLMRouter(
            backends={},
            providers={"openai": provider},
            default_backend="openai",
        )

        collected: list[str] = []

        async def cb(delta: str) -> None:
            collected.append(delta)

        request = LLMRequest(user_message="hi", backend_name="openai")
        result = await router.send(request, on_text_delta=cb)

        # 청크 단위 델타가 그대로 콜백에 흘러야 한다 — fallback 이면 ["Hello OpenAI"] 한 덩이.
        assert collected == ["Hello", " OpenAI"]
        assert result.text == "Hello OpenAI"
        assert result.backend_name == "openai"
        assert result.usage == {"input_tokens": 4, "output_tokens": 2}
