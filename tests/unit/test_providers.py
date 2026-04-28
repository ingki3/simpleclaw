"""Tests for LLM providers with mocked SDK calls."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from simpleclaw.llm.models import LLMAuthError, LLMProviderError, ToolCall, ToolDefinition
from simpleclaw.llm.providers.claude import ClaudeProvider
from simpleclaw.llm.providers.openai_provider import OpenAIProvider
from simpleclaw.llm.providers.gemini import GeminiProvider


# ---------------------------------------------------------------------------
# Shared fixture: sample ToolDefinition list
# ---------------------------------------------------------------------------

# 모든 Function Calling 테스트에서 공유하는 샘플 도구 정의.
# get_weather: 파라미터가 있는 일반적인 도구
# search: 빈 파라미터를 가진 도구 (각 프로바이더의 기본값 처리를 검증)
SAMPLE_TOOLS = [
    ToolDefinition(
        name="get_weather",
        description="Get current weather for a location",
        parameters={
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "City name"},
            },
            "required": ["location"],
        },
    ),
    ToolDefinition(
        name="search",
        description="Search the web",
        parameters={},
    ),
]


class TestClaudeProvider:
    def test_missing_api_key_raises(self):
        with pytest.raises(LLMAuthError):
            ClaudeProvider(model="claude-sonnet-4-20250514", api_key="")

    @pytest.mark.asyncio
    async def test_send_returns_response(self):
        provider = ClaudeProvider(
            model="claude-sonnet-4-20250514", api_key="test-key"
        )

        mock_message = MagicMock()
        text_block = MagicMock(type="text", text="Hello from Claude")
        mock_message.content = [text_block]
        mock_message.usage = MagicMock(input_tokens=10, output_tokens=5)

        provider._client.messages.create = AsyncMock(return_value=mock_message)

        result = await provider.send("system prompt", "hello")
        assert result.text == "Hello from Claude"
        assert result.backend_name == "claude"
        assert result.usage["input_tokens"] == 10

    @pytest.mark.asyncio
    async def test_auth_error(self):
        import anthropic
        provider = ClaudeProvider(model="test", api_key="bad-key")
        provider._client.messages.create = AsyncMock(
            side_effect=anthropic.AuthenticationError(
                message="Invalid API key",
                response=MagicMock(status_code=401),
                body={"error": {"message": "Invalid API key"}},
            )
        )
        with pytest.raises(LLMAuthError):
            await provider.send("sys", "msg")

    # -- Function Calling tests --

    def test_convert_tools(self):
        """ToolDefinition 리스트가 Claude API의 도구 형식으로 올바르게 변환되어야 한다."""
        result = ClaudeProvider._convert_tools(SAMPLE_TOOLS)
        assert len(result) == 2
        # Claude는 name, description, input_schema 키를 사용한다
        assert result[0]["name"] == "get_weather"
        assert result[0]["description"] == "Get current weather for a location"
        assert result[0]["input_schema"]["type"] == "object"
        assert "location" in result[0]["input_schema"]["properties"]
        # 빈 파라미터는 기본 스키마 {"type": "object", "properties": {}}로 변환되어야 한다
        assert result[1]["input_schema"] == {"type": "object", "properties": {}}

    def test_convert_messages_tool_result(self):
        """도구 실행 결과(role=tool)가 Claude의 tool_result 블록으로 변환되어야 한다.

        Claude API는 tool_result를 user 역할 안의 content 블록으로 전달해야 한다.
        """
        messages = [
            {"role": "tool", "tool_call_id": "call_123", "content": "sunny, 25C"},
        ]
        result = ClaudeProvider._convert_messages(messages)
        assert len(result) == 1
        # Claude는 tool_result를 user 역할로 감싸야 한다
        assert result[0]["role"] == "user"
        block = result[0]["content"][0]
        assert block["type"] == "tool_result"
        # tool_call_id가 tool_use_id로 매핑되어야 한다
        assert block["tool_use_id"] == "call_123"
        assert block["content"] == "sunny, 25C"

    def test_convert_messages_assistant_tool_calls(self):
        """assistant 메시지의 tool_calls가 Claude의 text + tool_use 블록으로 변환되어야 한다.

        Claude는 assistant 메시지의 content를 블록 배열로 표현하며,
        텍스트와 도구 호출이 별도 블록으로 분리되어야 한다.
        """
        messages = [
            {
                "role": "assistant",
                "content": "Let me check the weather.",
                "tool_calls": [
                    {"id": "tc_1", "name": "get_weather", "arguments": {"location": "Seoul"}},
                ],
            },
        ]
        result = ClaudeProvider._convert_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        content = result[0]["content"]
        # 첫 번째 블록은 텍스트 블록이어야 한다
        assert content[0]["type"] == "text"
        assert content[0]["text"] == "Let me check the weather."
        # 두 번째 블록은 tool_use 블록이어야 한다
        assert content[1]["type"] == "tool_use"
        assert content[1]["id"] == "tc_1"
        assert content[1]["name"] == "get_weather"
        # arguments가 input으로 매핑되어야 한다
        assert content[1]["input"] == {"location": "Seoul"}

    @pytest.mark.asyncio
    async def test_send_with_tool_calls(self):
        """도구가 포함된 send 호출 시, 응답에서 ToolCall 객체가 올바르게 파싱되어야 한다.

        Claude 응답에 text 블록과 tool_use 블록이 함께 있을 때,
        text는 result.text에, tool_use는 result.tool_calls에 담겨야 한다.
        """
        provider = ClaudeProvider(model="claude-sonnet-4-20250514", api_key="test-key")

        # Claude 응답 mock: text 블록 + tool_use 블록
        tool_use_block = MagicMock(type="tool_use", id="toolu_abc")
        tool_use_block.name = "get_weather"
        tool_use_block.input = {"location": "Seoul"}
        text_block = MagicMock(type="text", text="I'll look that up.")
        mock_message = MagicMock()
        mock_message.content = [text_block, tool_use_block]
        mock_message.usage = MagicMock(input_tokens=20, output_tokens=15)

        provider._client.messages.create = AsyncMock(return_value=mock_message)

        result = await provider.send("sys", "What's the weather?", tools=SAMPLE_TOOLS)
        # 텍스트 블록의 내용이 result.text에 담겨야 한다
        assert result.text == "I'll look that up."
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        tc = result.tool_calls[0]
        # 반환된 객체가 ToolCall 데이터 클래스여야 한다
        assert isinstance(tc, ToolCall)
        assert tc.id == "toolu_abc"
        assert tc.name == "get_weather"
        assert tc.arguments == {"location": "Seoul"}


class TestOpenAIProvider:
    def test_missing_api_key_raises(self):
        with pytest.raises(LLMAuthError):
            OpenAIProvider(model="gpt-4o", api_key="")

    @pytest.mark.asyncio
    async def test_send_returns_response(self):
        provider = OpenAIProvider(model="gpt-4o", api_key="test-key")

        mock_choice = MagicMock()
        mock_choice.message.content = "Hello from OpenAI"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = MagicMock(prompt_tokens=8, completion_tokens=4)

        provider._client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        result = await provider.send("system", "hello")
        assert result.text == "Hello from OpenAI"
        assert result.backend_name == "openai"

    # -- Function Calling tests --

    def test_convert_tools(self):
        """ToolDefinition 리스트가 OpenAI API의 도구 형식으로 올바르게 변환되어야 한다."""
        result = OpenAIProvider._convert_tools(SAMPLE_TOOLS)
        assert len(result) == 2
        # OpenAI는 type=function 래퍼 안에 function 객체를 둔다
        assert result[0]["type"] == "function"
        func = result[0]["function"]
        assert func["name"] == "get_weather"
        assert func["description"] == "Get current weather for a location"
        assert func["parameters"]["type"] == "object"
        # 빈 파라미터는 기본 스키마로 변환되어야 한다
        assert result[1]["function"]["parameters"] == {"type": "object", "properties": {}}

    def test_convert_messages_assistant_tool_calls(self):
        """assistant 메시지의 tool_calls가 OpenAI 형식(function.arguments=JSON 문자열)으로 변환되어야 한다.

        OpenAI API는 arguments를 JSON 문자열로 직렬화하여 전달해야 한다.
        """
        messages = [
            {
                "role": "assistant",
                "content": "Checking...",
                "tool_calls": [
                    {"id": "call_1", "name": "search", "arguments": {"query": "test"}},
                ],
            },
        ]
        result = OpenAIProvider._convert_messages(messages)
        assert len(result) == 1
        msg = result[0]
        assert msg["role"] == "assistant"
        assert msg["content"] == "Checking..."
        tc = msg["tool_calls"][0]
        assert tc["id"] == "call_1"
        # OpenAI는 tool_call에 type=function을 명시해야 한다
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "search"
        # arguments는 dict가 아닌 JSON 문자열로 직렬화되어야 한다
        assert json.loads(tc["function"]["arguments"]) == {"query": "test"}

    @pytest.mark.asyncio
    async def test_send_with_tool_calls(self):
        """도구가 포함된 send 호출 시, OpenAI 응답에서 ToolCall이 올바르게 파싱되어야 한다.

        응답의 message.content가 None이면 text는 빈 문자열이 되고,
        tool_calls에서 ToolCall 객체가 생성되어야 한다.
        """
        provider = OpenAIProvider(model="gpt-4o", api_key="test-key")

        # OpenAI 응답 mock: tool_call이 포함된 choice
        mock_tc = MagicMock()
        mock_tc.id = "call_xyz"
        mock_tc.function.name = "get_weather"
        mock_tc.function.arguments = '{"location": "Tokyo"}'

        mock_choice = MagicMock()
        # content가 None인 경우 — 모델이 텍스트 없이 도구만 호출한 경우
        mock_choice.message.content = None
        mock_choice.message.tool_calls = [mock_tc]
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = MagicMock(prompt_tokens=12, completion_tokens=8)

        provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await provider.send("sys", "weather?", tools=SAMPLE_TOOLS)
        # content=None이므로 text는 빈 문자열이어야 한다
        assert result.text == ""
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        tc = result.tool_calls[0]
        assert isinstance(tc, ToolCall)
        assert tc.id == "call_xyz"
        assert tc.name == "get_weather"
        # JSON 문자열이 dict로 파싱되어야 한다
        assert tc.arguments == {"location": "Tokyo"}


class TestGeminiProvider:
    def test_missing_api_key_raises(self):
        with pytest.raises(LLMAuthError):
            GeminiProvider(model="gemini-2.0-flash", api_key="")

    @pytest.mark.asyncio
    async def test_send_returns_response(self):
        provider = GeminiProvider(model="gemini-2.0-flash", api_key="test-key")

        # Gemini 응답 mock: candidates[0].content.parts[0].text 구조
        text_part = MagicMock()
        text_part.function_call = None
        text_part.text = "Hello from Gemini"

        content = MagicMock()
        content.parts = [text_part]

        candidate = MagicMock()
        candidate.content = content

        mock_response = MagicMock()
        mock_response.candidates = [candidate]
        mock_response.usage_metadata = MagicMock(
            prompt_token_count=7, candidates_token_count=3
        )

        provider._client.aio.models.generate_content = AsyncMock(
            return_value=mock_response
        )

        result = await provider.send("system", "hello")
        assert result.text == "Hello from Gemini"
        assert result.backend_name == "gemini"

    # -- Function Calling tests --

    def test_convert_tools(self):
        """ToolDefinition 리스트가 Gemini의 types.Tool(FunctionDeclaration 배열)로 변환되어야 한다.

        Gemini는 모든 함수 선언을 하나의 types.Tool 객체로 감싸서 전달한다.
        """
        from google.genai import types

        result = GeminiProvider._convert_tools(SAMPLE_TOOLS)
        # Gemini는 하나의 types.Tool 안에 모든 선언을 담는다
        assert len(result) == 1
        tool = result[0]
        assert isinstance(tool, types.Tool)
        decls = tool.function_declarations
        assert len(decls) == 2
        assert decls[0].name == "get_weather"
        assert decls[0].description == "Get current weather for a location"
        # 빈 파라미터는 None으로 변환되어야 한다 (Gemini는 빈 스키마를 지원하지 않음)
        assert decls[1].parameters is None

    def test_convert_messages_tool_result(self):
        """도구 실행 결과(role=tool)가 Gemini의 FunctionResponse 형식으로 변환되어야 한다.

        Gemini는 tool 결과를 user 역할의 function_response Part로 전달하며,
        문자열 결과는 {"result": ...} 딕셔너리로 감싸야 한다.
        """
        provider = GeminiProvider(model="gemini-2.0-flash", api_key="test-key")
        messages = [
            {"role": "tool", "name": "get_weather", "content": "rainy, 18C"},
        ]
        result = provider._convert_messages(messages)
        assert len(result) == 1
        c = result[0]
        # Gemini는 tool 결과를 user 역할로 전달한다
        assert c.role == "user"
        part = c.parts[0]
        assert part.function_response is not None
        assert part.function_response.name == "get_weather"
        # 문자열 결과가 {"result": ...} 딕셔너리로 감싸져야 한다
        assert part.function_response.response == {"result": "rainy, 18C"}

    def test_convert_messages_raw_content_passthrough(self):
        """_raw_content가 있는 assistant 메시지는 원본 객체를 그대로 통과시켜야 한다.

        Gemini의 thought_signature 등 SDK 고유 메타데이터를 보존하기 위해,
        _raw_content 키가 있으면 변환 없이 원본을 사용한다.
        """
        provider = GeminiProvider(model="gemini-2.0-flash", api_key="test-key")
        raw_obj = MagicMock(name="raw_content_object")
        messages = [
            {
                "role": "assistant",
                "content": "thinking...",
                "tool_calls": [{"id": "x", "name": "search", "arguments": {}}],
                "_raw_content": raw_obj,
            },
        ]
        result = provider._convert_messages(messages)
        assert len(result) == 1
        # 변환 없이 원본 객체가 그대로 전달되어야 한다
        assert result[0] is raw_obj

    @pytest.mark.asyncio
    async def test_send_with_function_call(self):
        """도구가 포함된 send 호출 시, Gemini 응답에서 ToolCall이 올바르게 파싱되어야 한다.

        Gemini는 FunctionCall Part를 통해 도구 호출을 반환하며,
        ID가 없으므로 UUID가 자동 생성되어야 한다.
        또한 raw_assistant_message에 원본 content가 보존되어야 한다.
        """
        provider = GeminiProvider(model="gemini-2.0-flash", api_key="test-key")

        # Gemini 응답 mock: text Part + FunctionCall Part
        fc_part = MagicMock()
        fc_part.function_call = MagicMock()
        fc_part.function_call.name = "get_weather"
        fc_part.function_call.args = {"location": "Busan"}
        fc_part.text = None

        text_part = MagicMock()
        text_part.function_call = None
        text_part.text = "Let me check."

        content = MagicMock()
        content.parts = [text_part, fc_part]

        candidate = MagicMock()
        candidate.content = content

        mock_response = MagicMock()
        mock_response.candidates = [candidate]
        mock_response.usage_metadata = MagicMock(
            prompt_token_count=15, candidates_token_count=10
        )

        provider._client.aio.models.generate_content = AsyncMock(
            return_value=mock_response
        )

        result = await provider.send("sys", "weather?", tools=SAMPLE_TOOLS)
        assert result.text == "Let me check."
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        tc = result.tool_calls[0]
        assert isinstance(tc, ToolCall)
        assert tc.name == "get_weather"
        assert tc.arguments == {"location": "Busan"}
        # Gemini는 tool_call ID를 제공하지 않으므로 UUID가 자동 생성되어야 한다
        assert tc.id
        # 멀티턴 대화에서 thought_signature 보존을 위해 원본 content가 저장되어야 한다
        assert result.raw_assistant_message is content
