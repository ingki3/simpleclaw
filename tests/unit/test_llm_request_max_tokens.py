"""BIZ-297 — LLMRequest.max_tokens propagation tests.

호출자가 ``LLMRequest.max_tokens`` 를 지정하면 그 값이 각 프로바이더의 API 호출
kwargs 까지 끊김 없이 흘러야 한다. 미지정 시(=None)에는 기존 하드코딩 기본값
(Claude 4096) 이 유지되어 회귀가 0 임을 검증한다 — dreaming 등 기존 호출처가
``max_tokens`` 를 추가하지 않은 채로도 그대로 동작해야 한다.

검증 매트릭스:
- LLMRequest: 인스턴스가 필드를 보존하는가
- ClaudeProvider.send/stream: kwargs["max_tokens"] = 요청값 or 4096
- OpenAIProvider.send/stream: kwargs[max_tokens or max_completion_tokens] (o1/o3 분기)
- GeminiProvider.send/stream: config.max_output_tokens 매핑
- 라우터: LLMRequest.max_tokens 가 provider.send/stream 의 max_tokens kwarg 로
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.llm.models import LLMRequest, LLMResponse, SystemBlock
from simpleclaw.llm.providers.base import LLMProvider
from simpleclaw.llm.providers.claude import ClaudeProvider
from simpleclaw.llm.providers.gemini import GeminiProvider
from simpleclaw.llm.providers.openai_provider import (
    OpenAIProvider,
    _max_tokens_field,
)
from simpleclaw.llm.router import LLMRouter


# ---------------------------------------------------------------------------
# 1. LLMRequest 모델 자체
# ---------------------------------------------------------------------------


class TestLLMRequestField:
    def test_default_max_tokens_is_none(self):
        """max_tokens 미지정 시 기본값은 None — 기존 호출처 회귀 0."""
        req = LLMRequest(user_message="hi")
        assert req.max_tokens is None

    def test_max_tokens_stored_verbatim(self):
        """전달된 값이 LLMRequest 객체에 그대로 보존되어야 한다."""
        req = LLMRequest(user_message="hi", max_tokens=1024)
        assert req.max_tokens == 1024


# ---------------------------------------------------------------------------
# 2. ClaudeProvider — claude.py:123 / :220 두 곳 모두 패치 검증
# ---------------------------------------------------------------------------


def _build_claude_mock_message() -> MagicMock:
    msg = MagicMock()
    msg.content = [MagicMock(type="text", text="ok")]
    msg.usage = MagicMock(input_tokens=1, output_tokens=1)
    del msg.usage.cache_creation_input_tokens
    del msg.usage.cache_read_input_tokens
    return msg


class TestClaudeMaxTokens:
    @pytest.mark.asyncio
    async def test_send_default_uses_4096_fallback(self):
        """max_tokens 미지정 시 기존 하드코딩값 4096 이 유지되어야 한다 (회귀 0)."""
        provider = ClaudeProvider(model="claude-sonnet-4-20250514", api_key="k")
        create = AsyncMock(return_value=_build_claude_mock_message())
        provider._client.messages.create = create

        await provider.send("sys", "msg")
        assert create.call_args.kwargs["max_tokens"] == 4096

    @pytest.mark.asyncio
    async def test_send_uses_explicit_max_tokens(self):
        """요청 max_tokens 가 그대로 Anthropic kwargs 에 박혀야 한다."""
        provider = ClaudeProvider(model="claude-sonnet-4-20250514", api_key="k")
        create = AsyncMock(return_value=_build_claude_mock_message())
        provider._client.messages.create = create

        await provider.send("sys", "msg", max_tokens=777)
        assert create.call_args.kwargs["max_tokens"] == 777

    @pytest.mark.asyncio
    async def test_send_zero_or_none_falls_back_to_4096(self):
        """0 / None 은 비활성으로 간주, 기본값 4096 으로 떨어져야 한다."""
        provider = ClaudeProvider(model="claude-sonnet-4-20250514", api_key="k")
        create = AsyncMock(return_value=_build_claude_mock_message())
        provider._client.messages.create = create

        await provider.send("sys", "msg", max_tokens=0)
        assert create.call_args.kwargs["max_tokens"] == 4096

        create.reset_mock()
        await provider.send("sys", "msg", max_tokens=None)
        assert create.call_args.kwargs["max_tokens"] == 4096

    @pytest.mark.asyncio
    async def test_stream_default_uses_4096_fallback(self):
        """stream() 도 max_tokens 미지정 시 기존 4096 유지 (claude.py:220)."""
        provider = ClaudeProvider(model="claude-sonnet-4-20250514", api_key="k")

        final = MagicMock()
        final.content = []
        final.usage = MagicMock(input_tokens=1, output_tokens=1)
        del final.usage.cache_creation_input_tokens
        del final.usage.cache_read_input_tokens

        class _Ctx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def __aiter__(self):
                async def gen():
                    if False:
                        yield  # pragma: no cover
                return gen()

            async def get_final_message(self):
                return final

        stream_mock = MagicMock(return_value=_Ctx())
        provider._client.messages.stream = stream_mock

        await provider.stream("sys", "msg")
        assert stream_mock.call_args.kwargs["max_tokens"] == 4096

    @pytest.mark.asyncio
    async def test_stream_uses_explicit_max_tokens(self):
        provider = ClaudeProvider(model="claude-sonnet-4-20250514", api_key="k")

        final = MagicMock()
        final.content = []
        final.usage = MagicMock(input_tokens=1, output_tokens=1)
        del final.usage.cache_creation_input_tokens
        del final.usage.cache_read_input_tokens

        class _Ctx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def __aiter__(self):
                async def gen():
                    if False:
                        yield  # pragma: no cover
                return gen()

            async def get_final_message(self):
                return final

        stream_mock = MagicMock(return_value=_Ctx())
        provider._client.messages.stream = stream_mock

        await provider.stream("sys", "msg", max_tokens=2048)
        assert stream_mock.call_args.kwargs["max_tokens"] == 2048


# ---------------------------------------------------------------------------
# 3. OpenAIProvider — gpt-* 는 max_tokens, o1/o3 는 max_completion_tokens
# ---------------------------------------------------------------------------


def _build_openai_mock_response() -> MagicMock:
    choice = MagicMock()
    choice.message.content = "ok"
    choice.message.tool_calls = None
    response = MagicMock()
    response.choices = [choice]
    response.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
    return response


class TestOpenAIMaxTokens:
    def test_field_dispatch_gpt(self):
        """gpt-* / 디폴트 모델은 ``max_tokens`` 필드를 쓴다."""
        assert _max_tokens_field("gpt-4o") == "max_tokens"
        assert _max_tokens_field("gpt-4o-mini") == "max_tokens"
        assert _max_tokens_field("gpt-3.5-turbo") == "max_tokens"
        assert _max_tokens_field("") == "max_tokens"

    def test_field_dispatch_o1_o3(self):
        """o1/o3 reasoning 모델은 ``max_completion_tokens`` 가 필수."""
        assert _max_tokens_field("o1") == "max_completion_tokens"
        assert _max_tokens_field("o1-mini") == "max_completion_tokens"
        assert _max_tokens_field("o1-preview") == "max_completion_tokens"
        assert _max_tokens_field("o3") == "max_completion_tokens"
        assert _max_tokens_field("o3-mini") == "max_completion_tokens"

    @pytest.mark.asyncio
    async def test_send_default_omits_max_tokens(self):
        """max_tokens 미지정 시 API kwargs 에 어느 필드도 박히면 안 된다 (회귀 0)."""
        provider = OpenAIProvider(model="gpt-4o", api_key="k")
        create = AsyncMock(return_value=_build_openai_mock_response())
        provider._client.chat.completions.create = create

        await provider.send("sys", "msg")
        kwargs = create.call_args.kwargs
        assert "max_tokens" not in kwargs
        assert "max_completion_tokens" not in kwargs

    @pytest.mark.asyncio
    async def test_send_gpt_uses_max_tokens_field(self):
        provider = OpenAIProvider(model="gpt-4o", api_key="k")
        create = AsyncMock(return_value=_build_openai_mock_response())
        provider._client.chat.completions.create = create

        await provider.send("sys", "msg", max_tokens=512)
        kwargs = create.call_args.kwargs
        assert kwargs["max_tokens"] == 512
        assert "max_completion_tokens" not in kwargs

    @pytest.mark.asyncio
    async def test_send_o1_uses_max_completion_tokens(self):
        """o1 모델은 ``max_completion_tokens`` 로 매핑되어야 한다."""
        provider = OpenAIProvider(model="o1-mini", api_key="k")
        create = AsyncMock(return_value=_build_openai_mock_response())
        provider._client.chat.completions.create = create

        await provider.send("sys", "msg", max_tokens=2048)
        kwargs = create.call_args.kwargs
        assert kwargs["max_completion_tokens"] == 2048
        assert "max_tokens" not in kwargs

    @pytest.mark.asyncio
    async def test_stream_uses_max_tokens(self):
        provider = OpenAIProvider(model="gpt-4o", api_key="k")

        usage_chunk = MagicMock()
        usage_chunk.choices = []
        usage_chunk.usage = MagicMock(prompt_tokens=1, completion_tokens=1)

        class _Iter:
            def __aiter__(self):
                async def gen():
                    yield usage_chunk
                return gen()

        create = AsyncMock(return_value=_Iter())
        provider._client.chat.completions.create = create

        await provider.stream("sys", "msg", max_tokens=999)
        kwargs = create.call_args.kwargs
        assert kwargs["max_tokens"] == 999


# ---------------------------------------------------------------------------
# 4. GeminiProvider — config.max_output_tokens 매핑
# ---------------------------------------------------------------------------


def _build_gemini_mock_response(text: str = "ok") -> MagicMock:
    text_part = MagicMock()
    text_part.function_call = None
    text_part.text = text

    content = MagicMock()
    content.parts = [text_part]

    candidate = MagicMock()
    candidate.content = content

    response = MagicMock()
    response.candidates = [candidate]
    response.usage_metadata = MagicMock(
        prompt_token_count=1, candidates_token_count=1
    )
    return response


class TestGeminiMaxTokens:
    @pytest.mark.asyncio
    async def test_send_default_leaves_config_default(self):
        """max_tokens 미지정 시 max_output_tokens 가 설정되지 않아야 한다 (모델 기본값)."""
        provider = GeminiProvider(model="gemini-2.0-flash", api_key="k")
        generate = AsyncMock(return_value=_build_gemini_mock_response())
        provider._client.aio.models.generate_content = generate

        await provider.send("sys", "msg")
        config = generate.call_args.kwargs["config"]
        # SDK 의 기본은 None — 명시적으로 박지 않아 모델 기본값에 맡긴다.
        assert getattr(config, "max_output_tokens", None) is None

    @pytest.mark.asyncio
    async def test_send_uses_explicit_max_tokens(self):
        """max_tokens 지정 시 GenerateContentConfig.max_output_tokens 가 그 값이어야."""
        provider = GeminiProvider(model="gemini-2.0-flash", api_key="k")
        generate = AsyncMock(return_value=_build_gemini_mock_response())
        provider._client.aio.models.generate_content = generate

        await provider.send("sys", "msg", max_tokens=600)
        config = generate.call_args.kwargs["config"]
        assert config.max_output_tokens == 600

    @pytest.mark.asyncio
    async def test_stream_uses_explicit_max_tokens(self):
        provider = GeminiProvider(model="gemini-2.0-flash", api_key="k")

        usage_chunk = MagicMock()
        usage_chunk.candidates = []
        usage_chunk.usage_metadata = MagicMock(
            prompt_token_count=1, candidates_token_count=1
        )

        class _Iter:
            def __aiter__(self):
                async def gen():
                    yield usage_chunk
                return gen()

        stream = AsyncMock(return_value=_Iter())
        provider._client.aio.models.generate_content_stream = stream

        await provider.stream("sys", "msg", max_tokens=300)
        config = stream.call_args.kwargs["config"]
        assert config.max_output_tokens == 300


# ---------------------------------------------------------------------------
# 5. 라우터 통합 — LLMRequest.max_tokens 가 provider.send/stream 까지
# ---------------------------------------------------------------------------


class _CapturingProvider(LLMProvider):
    """send/stream 의 max_tokens kwarg 값을 캡처하는 미니 프로바이더."""

    def __init__(self):
        self.last_send_max_tokens: int | None = "unset"  # type: ignore[assignment]
        self.last_stream_max_tokens: int | None = "unset"  # type: ignore[assignment]

    async def send(
        self,
        system_prompt: str,
        user_message: str,
        messages=None,
        tools=None,
        system_blocks=None,
        max_tokens: int | None = None,
        **kwargs,  # BIZ-427 structured output 등 신규 optional kwargs 허용
    ) -> LLMResponse:
        self.last_send_max_tokens = max_tokens
        return LLMResponse(text="ok", backend_name="cap")

    async def stream(
        self,
        system_prompt: str,
        user_message: str,
        messages=None,
        tools=None,
        system_blocks=None,
        on_text_delta=None,
        max_tokens: int | None = None,
        **kwargs,  # BIZ-427 structured output 등 신규 optional kwargs 허용
    ) -> LLMResponse:
        self.last_stream_max_tokens = max_tokens
        return LLMResponse(text="ok", backend_name="cap")


class TestRouterPropagatesMaxTokens:
    @pytest.mark.asyncio
    async def test_send_passes_request_max_tokens(self):
        provider = _CapturingProvider()
        router = LLMRouter(
            backends={}, providers={"cap": provider}, default_backend="cap"
        )

        await router.send(LLMRequest(user_message="hi", max_tokens=512))
        assert provider.last_send_max_tokens == 512

    @pytest.mark.asyncio
    async def test_send_default_is_none(self):
        """max_tokens 미지정 LLMRequest → provider 가 None 을 받는다 (호환성 1순위)."""
        provider = _CapturingProvider()
        router = LLMRouter(
            backends={}, providers={"cap": provider}, default_backend="cap"
        )

        await router.send(LLMRequest(user_message="hi"))
        assert provider.last_send_max_tokens is None

    @pytest.mark.asyncio
    async def test_stream_passes_request_max_tokens(self):
        provider = _CapturingProvider()
        router = LLMRouter(
            backends={}, providers={"cap": provider}, default_backend="cap"
        )

        async def cb(_d: str) -> None:
            pass

        await router.send(
            LLMRequest(user_message="hi", max_tokens=2048), on_text_delta=cb
        )
        assert provider.last_stream_max_tokens == 2048


# ---------------------------------------------------------------------------
# 6. system_blocks 와 max_tokens 가 같은 요청에서 공존
# ---------------------------------------------------------------------------


class TestComboWithSystemBlocks:
    @pytest.mark.asyncio
    async def test_claude_send_with_blocks_and_max_tokens(self):
        """BIZ-252 system_blocks 와 BIZ-297 max_tokens 가 같은 호출에 함께 가야 한다.

        Anthropic kwargs 에 cache_control 마커와 max_tokens 가 동시에 들어가야 한다.
        """
        provider = ClaudeProvider(model="claude-sonnet-4-20250514", api_key="k")
        create = AsyncMock(return_value=_build_claude_mock_message())
        provider._client.messages.create = create

        blocks = [SystemBlock(text="persona", cache=True)]
        await provider.send("", "hi", system_blocks=blocks, max_tokens=1500)
        kwargs = create.call_args.kwargs
        assert kwargs["max_tokens"] == 1500
        assert kwargs["system"][0]["cache_control"]["ttl"] == "1h"
