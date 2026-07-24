"""Vertex AI Gemini 프로바이더 — ADC/service account 기반 OAuth 인증 (BIZ-444).

GeminiProvider(Google AI Studio, static API key)와 동일한 google-genai SDK를
사용하되, 클라이언트를 Vertex AI 백엔드(``vertexai=True``)로 초기화한다.
요청 페이로드 변환, 응답 파싱, Native Function Calling, structured output,
스트리밍, thought_signature 보존 경로는 전부 GeminiProvider를 그대로
상속한다 — 두 백엔드의 차이는 인증 방식과 엔드포인트뿐이다.

인증 정책 (우선순위 순):
  1. ``credentials_path`` — service account JSON 파일 경로. google-auth의
     ``service_account.Credentials``로 로드한다. project 미지정 시 SA JSON의
     ``project_id``를 사용한다.
  2. ``project`` — 명시 프로젝트 + ADC(Application Default Credentials).
     ``gcloud auth application-default login`` 또는
     ``GOOGLE_APPLICATION_CREDENTIALS`` 환경변수로 준비한다.
  3. ``api_key`` — Vertex AI express mode. SDK가 project/location과 상호
     배타로 강제하므로 project가 있으면 api_key는 무시한다 (IAM 경로 우선).
  4. 전부 없으면 SDK가 ADC에서 credentials와 project를 자동 발견한다.
     실패하면 LLMAuthError로 fail-fast — 라우터가 이 프로바이더만 skip 한다.

토큰 수명 관리:
  access token의 mint/refresh는 google-auth transport가 매 요청 시점에
  자동 수행한다 (만료 임박 시 재발급, 동시성은 SDK 내부 auth lock으로 보호).
  SimpleClaw는 토큰을 직접 발급/저장/캐시하지 않는다 — 이 프로바이더에
  토큰 상태가 없어야 키 회전(SA 키 교체·ADC 재로그인)이 프로세스 재시작
  없이도 안전하다.
"""

from __future__ import annotations

from pathlib import Path

from google import genai
from google.auth.exceptions import GoogleAuthError
from google.oauth2 import service_account

from simpleclaw.llm.models import LLMAuthError, LLMProviderError
from simpleclaw.llm.profiles import ProviderProfile, get_provider_profile
from simpleclaw.llm.providers.gemini import GeminiProvider

# service account 토큰에 부여할 OAuth scope — Vertex AI 호출의 표준 scope.
_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

# location 미지정 시 기본값 — global endpoint는 리전 고정 없이 Gemini 모델을
# 서빙하므로 리전 quota 관리가 필요 없는 기본 구성에 적합하다.
_DEFAULT_LOCATION = "global"


class VertexGeminiProvider(GeminiProvider):
    """Vertex AI 백엔드로 통신하는 Gemini 프로바이더."""

    # 라우터가 config.yaml provider 블록에서 그대로 넘겨줄 추가 설정 키.
    EXTRA_CONFIG_KEYS = ("project", "location", "credentials_path")

    def __init__(
        self,
        model: str,
        api_key: str = "",
        name: str = "vertex_gemini",
        project: str | None = None,
        location: str | None = None,
        credentials_path: str | None = None,
        profile: ProviderProfile | None = None,
    ) -> None:
        """VertexGeminiProvider를 초기화한다.

        Args:
            model: 사용할 Gemini 모델 ID (예: gemini-3.5-flash).
            api_key: Vertex express mode용 API 키 (선택). project/credentials가
                있으면 무시된다 — 시그니처는 라우터의 공통 생성 규약과 통일.
            name: 라우터에서 이 백엔드를 식별하는 이름.
            project: GCP 프로젝트 ID. 생략 시 SA JSON 또는 ADC에서 발견.
            location: Vertex 리전. 생략 시 "global".
            credentials_path: service account JSON 파일 경로. 생략 시 ADC.

        Raises:
            LLMAuthError: SA 파일이 없거나 로드 실패, 또는 ADC 자동 발견 실패.
        """
        self._model = model
        self._name = name
        self._profile = profile or get_provider_profile("gemini")

        credentials = None
        if credentials_path:
            sa_path = Path(credentials_path).expanduser()
            if not sa_path.is_file():
                raise LLMAuthError(
                    f"Service account file not found for provider '{name}': {sa_path}"
                )
            try:
                credentials = service_account.Credentials.from_service_account_file(
                    str(sa_path), scopes=[_CLOUD_PLATFORM_SCOPE]
                )
            except Exception as e:
                raise LLMAuthError(
                    f"Failed to load service account credentials "
                    f"for provider '{name}': {e}"
                ) from e
            # SA JSON의 project_id 를 fallback 으로 사용 — SDK 는 명시
            # credentials 가 있어도 project 가 비면 ADC 를 다시 조회하므로
            # 여기서 채워 이중 조회를 막는다.
            if not project:
                project = getattr(credentials, "project_id", None)

        client_kwargs: dict = {"vertexai": True}
        if credentials is not None or project:
            # IAM 경로 — SDK 가 project/location 과 api_key 를 상호 배타로
            # 강제(ValueError)하므로 api_key 는 여기서 명시적으로 배제한다.
            client_kwargs["credentials"] = credentials
            client_kwargs["project"] = project
            client_kwargs["location"] = location or _DEFAULT_LOCATION
        elif api_key:
            # Vertex express mode — 조직 IAM 없이 API 키만으로 Vertex 사용.
            client_kwargs["api_key"] = api_key
        elif location:
            # ADC 자동 발견 경로 — project 는 SDK 가 ADC 에서 채운다.
            client_kwargs["location"] = location

        try:
            self._client = genai.Client(**client_kwargs)
        except Exception as e:
            # ADC 미구성(DefaultCredentialsError) 등 — 초기화 시점 fail-fast 로
            # 라우터가 이 프로바이더만 skip 하고 나머지 백엔드는 살린다.
            raise LLMAuthError(
                f"Vertex AI client init failed for provider '{name}': {e}"
            ) from e

    @classmethod
    def _map_provider_error(cls, e: Exception) -> LLMProviderError:
        """google-auth 예외를 인증 에러로 추가 분류한다.

        RefreshError/DefaultCredentialsError 등은 타입 이름에 "auth"가 없어
        부모의 이름 기반 판별을 통과하지 못하므로 isinstance 로 잡는다 —
        토큰 refresh 실패가 일반 API 에러로 오분류되면 호출 측 재시도
        정책이 무의미한 재시도를 반복하게 된다.
        """
        if isinstance(e, GoogleAuthError):
            return LLMAuthError(f"Vertex AI auth failed: {e}")
        return super()._map_provider_error(e)
