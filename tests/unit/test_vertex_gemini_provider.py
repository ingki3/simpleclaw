"""VertexGeminiProvider 단위 테스트 (BIZ-444).

Vertex AI Gemini 프로바이더의 인증 경로(SA JSON/ADC/express mode) 선택,
토큰 refresh 위임(google-auth), 에러 매핑, 라우터 등록을 검증한다.
네트워크/실제 GCP 자격증명 없이 genai.Client 와 service_account 로더를
mock 하여 실행한다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.auth.exceptions import DefaultCredentialsError, RefreshError

from simpleclaw.llm.models import LLMAuthError, LLMProviderError
from simpleclaw.llm.providers.vertex_gemini import VertexGeminiProvider


def _make_provider(**kwargs):
    """genai.Client 를 mock 한 채 프로바이더를 생성하고 (provider, Client mock) 반환."""
    with patch("simpleclaw.llm.providers.vertex_gemini.genai.Client") as client_cls:
        provider = VertexGeminiProvider(model="gemini-3.5-flash", **kwargs)
    return provider, client_cls


class TestVertexGeminiInit:
    def test_explicit_project_uses_adc_and_default_location(self):
        """project 만 주면 ADC 경로 — credentials=None, location 기본값 global."""
        _, client_cls = _make_provider(project="test-proj")
        client_cls.assert_called_once_with(
            vertexai=True,
            credentials=None,
            project="test-proj",
            location="global",
        )

    def test_location_override(self):
        _, client_cls = _make_provider(project="test-proj", location="us-central1")
        assert client_cls.call_args.kwargs["location"] == "us-central1"

    def test_api_key_express_mode(self):
        """project/credentials 가 없고 api_key 만 있으면 Vertex express mode."""
        _, client_cls = _make_provider(api_key="express-key")
        client_cls.assert_called_once_with(vertexai=True, api_key="express-key")

    def test_project_takes_precedence_over_api_key(self):
        """SDK 는 project 와 api_key 를 상호 배타로 강제하므로 IAM 경로를 우선한다."""
        _, client_cls = _make_provider(project="test-proj", api_key="express-key")
        assert "api_key" not in client_cls.call_args.kwargs
        assert client_cls.call_args.kwargs["project"] == "test-proj"

    def test_no_config_delegates_discovery_to_sdk(self):
        """project/api_key/SA 전부 없으면 SDK 의 ADC 자동 발견에 맡긴다."""
        _, client_cls = _make_provider()
        client_cls.assert_called_once_with(vertexai=True)

    def test_client_init_failure_maps_to_auth_error(self):
        """ADC 미구성 등 초기화 실패는 LLMAuthError — 라우터가 skip 할 수 있어야 한다."""
        with patch(
            "simpleclaw.llm.providers.vertex_gemini.genai.Client",
            side_effect=DefaultCredentialsError("no ADC"),
        ), pytest.raises(LLMAuthError):
            VertexGeminiProvider(model="gemini-3.5-flash", project="test-proj")


class TestVertexGeminiServiceAccount:
    def test_service_account_credentials_passed_verbatim(self, tmp_path):
        """SA JSON 로드 결과가 그대로 Client 에 전달되어야 한다.

        토큰 mint/refresh 는 google-auth 가 요청 시점에 수행하므로, 프로바이더는
        credentials 객체를 가공/캐시 없이 위임하는 것이 계약이다.
        """
        sa_file = tmp_path / "sa.json"
        sa_file.write_text("{}")

        fake_creds = MagicMock()
        fake_creds.project_id = "sa-json-project"
        with patch(
            "simpleclaw.llm.providers.vertex_gemini."
            "service_account.Credentials.from_service_account_file",
            return_value=fake_creds,
        ) as loader:
            _, client_cls = _make_provider(credentials_path=str(sa_file))

        loader.assert_called_once_with(
            str(sa_file),
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        kwargs = client_cls.call_args.kwargs
        # credentials 객체가 정확히 그대로 전달 — 토큰 상태를 직접 들지 않는다.
        assert kwargs["credentials"] is fake_creds
        # project 미지정 시 SA JSON 의 project_id 를 fallback 으로 사용.
        assert kwargs["project"] == "sa-json-project"

    def test_explicit_project_overrides_sa_project_id(self, tmp_path):
        sa_file = tmp_path / "sa.json"
        sa_file.write_text("{}")
        fake_creds = MagicMock()
        fake_creds.project_id = "sa-json-project"
        with patch(
            "simpleclaw.llm.providers.vertex_gemini."
            "service_account.Credentials.from_service_account_file",
            return_value=fake_creds,
        ):
            _, client_cls = _make_provider(
                credentials_path=str(sa_file), project="explicit-proj"
            )
        assert client_cls.call_args.kwargs["project"] == "explicit-proj"

    def test_missing_service_account_file_raises(self, tmp_path):
        with pytest.raises(LLMAuthError):
            VertexGeminiProvider(
                model="gemini-3.5-flash",
                credentials_path=str(tmp_path / "missing.json"),
            )

    def test_broken_service_account_file_raises(self, tmp_path):
        """SA JSON 파싱 실패도 LLMAuthError 로 매핑되어야 한다."""
        sa_file = tmp_path / "sa.json"
        sa_file.write_text("not-json")
        with patch(
            "simpleclaw.llm.providers.vertex_gemini."
            "service_account.Credentials.from_service_account_file",
            side_effect=ValueError("bad key file"),
        ), pytest.raises(LLMAuthError):
            VertexGeminiProvider(
                model="gemini-3.5-flash", credentials_path=str(sa_file)
            )


class TestVertexGeminiSend:
    """상속된 send() 경로가 Vertex 백엔드 이름/에러 매핑으로 동작하는지 검증."""

    @staticmethod
    def _mock_text_response(text: str) -> MagicMock:
        part = MagicMock()
        part.function_call = None
        part.text = text
        content = MagicMock()
        content.parts = [part]
        candidate = MagicMock()
        candidate.content = content
        response = MagicMock()
        response.candidates = [candidate]
        response.usage_metadata = MagicMock(
            prompt_token_count=7, candidates_token_count=3
        )
        return response

    @pytest.mark.asyncio
    async def test_send_returns_response_with_vertex_backend_name(self):
        provider, _ = _make_provider(project="test-proj")
        provider._client.aio.models.generate_content = AsyncMock(
            return_value=self._mock_text_response("Hello from Vertex")
        )
        result = await provider.send("system", "hello")
        assert result.text == "Hello from Vertex"
        assert result.backend_name == "vertex_gemini"

    @pytest.mark.asyncio
    async def test_token_refresh_failure_maps_to_auth_error(self):
        """google-auth RefreshError 는 이름 기반 판별을 통과하지 못하므로
        Vertex 전용 isinstance 매핑으로 LLMAuthError 가 되어야 한다."""
        provider, _ = _make_provider(project="test-proj")
        provider._client.aio.models.generate_content = AsyncMock(
            side_effect=RefreshError("token expired")
        )
        with pytest.raises(LLMAuthError):
            await provider.send("system", "hello")

    @pytest.mark.asyncio
    async def test_non_auth_error_stays_provider_error(self):
        provider, _ = _make_provider(project="test-proj")
        provider._client.aio.models.generate_content = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        with pytest.raises(LLMProviderError) as excinfo:
            await provider.send("system", "hello")
        assert not isinstance(excinfo.value, LLMAuthError)

    @pytest.mark.asyncio
    async def test_structured_output_config_inherited(self):
        """BIZ-427 structured output 매핑이 Vertex 경로에서도 동일해야 한다."""
        provider, _ = _make_provider(project="test-proj")
        provider._client.aio.models.generate_content = AsyncMock(
            return_value=self._mock_text_response('{"ok": true}')
        )
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
        await provider.send(
            "system",
            "hello",
            response_mime_type="application/json",
            response_schema=schema,
            require_structured_output=True,
        )
        config = provider._client.aio.models.generate_content.call_args.kwargs["config"]
        assert config.response_mime_type == "application/json"
        assert config.response_schema == schema


class TestVertexGeminiRouterRegistration:
    def test_registry_contains_vertex_gemini(self):
        from simpleclaw.llm import router as router_mod

        router_mod._ensure_registry()
        assert router_mod._PROVIDER_REGISTRY["vertex_gemini"] is VertexGeminiProvider

    def test_create_router_passes_extra_config_keys(self, tmp_path):
        """config.yaml 의 project/location/credentials_path 가 프로바이더까지
        전달되고, 백엔드로 정상 등록되어야 한다."""
        from simpleclaw.llm.router import create_router

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "llm:\n"
            "  default: vertex_gemini\n"
            "  providers:\n"
            "    vertex_gemini:\n"
            '      type: "api"\n'
            '      model: "gemini-3.5-flash"\n'
            '      project: "router-proj"\n'
            '      location: "us-central1"\n',
            encoding="utf-8",
        )
        with patch(
            "simpleclaw.llm.providers.vertex_gemini.genai.Client"
        ) as client_cls:
            router = create_router(config_file)

        assert "vertex_gemini" in router.list_backends()
        assert router.get_default_backend() == "vertex_gemini"
        kwargs = client_cls.call_args.kwargs
        assert kwargs["project"] == "router-proj"
        assert kwargs["location"] == "us-central1"

    def test_create_router_skips_vertex_on_auth_failure(self, tmp_path):
        """Vertex 초기화 실패 시 해당 프로바이더만 skip — 부분 가용성 보장."""
        from simpleclaw.llm.router import create_router

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "llm:\n"
            "  default: gemini\n"
            "  providers:\n"
            "    gemini:\n"
            '      type: "api"\n'
            '      model: "gemini-3.5-flash"\n'
            '      api_key: "test-key"\n'
            "    vertex_gemini:\n"
            '      type: "api"\n'
            '      model: "gemini-3.5-flash"\n',
            encoding="utf-8",
        )
        # genai.Client 는 gemini/vertex_gemini 가 공유하는 모듈 속성이므로
        # vertexai=True 호출만 실패시킨다 — gemini 프로바이더는 살아야 한다.
        def fake_client(**kwargs):
            if kwargs.get("vertexai"):
                raise DefaultCredentialsError("no ADC")
            return MagicMock()

        with patch(
            "simpleclaw.llm.providers.vertex_gemini.genai.Client",
            side_effect=fake_client,
        ):
            router = create_router(config_file)

        backends = router.list_backends()
        assert "gemini" in backends
        assert "vertex_gemini" not in backends
