"""LLM 라우터: 설정 기반 백엔드 선택 및 메시지 라우팅.

config.yaml의 llm 섹션을 읽어 여러 LLM 프로바이더를 초기화하고,
요청마다 지정된(또는 기본) 백엔드로 메시지를 전달한다.

설계 결정:
  - 프로바이더 레지스트리를 지연 임포트로 구성하여 순환 임포트 방지
  - 초기화 실패한 프로바이더는 경고만 남기고 건너뛰어 부분 가용성 보장
"""

from __future__ import annotations

import logging
from pathlib import Path

from simpleclaw.config import load_llm_config
from simpleclaw.llm.models import (
    BackendType,
    LLMBackend,
    LLMConfigError,
    LLMRequest,
    LLMResponse,
)
from simpleclaw.llm.providers.base import LLMProvider

logger = logging.getLogger(__name__)

# 프로바이더 이름 → 클래스 매핑. _ensure_registry()에서 지연 채워짐.
_PROVIDER_REGISTRY: dict[str, type] = {}


def _ensure_registry() -> None:
    """프로바이더 레지스트리를 지연 로드한다.

    순환 임포트를 피하고 선택적 의존성(openai, anthropic 등)을
    실제 사용 시점까지 미루기 위해 최초 호출 시에만 임포트한다.
    """
    if _PROVIDER_REGISTRY:
        return
    from simpleclaw.llm.providers.claude import ClaudeProvider
    from simpleclaw.llm.providers.openai_provider import OpenAIProvider
    from simpleclaw.llm.providers.gemini import GeminiProvider
    from simpleclaw.llm.cli_wrapper import CLIProvider

    _PROVIDER_REGISTRY["claude"] = ClaudeProvider
    _PROVIDER_REGISTRY["openai"] = OpenAIProvider
    _PROVIDER_REGISTRY["gemini"] = GeminiProvider
    _PROVIDER_REGISTRY["cli"] = CLIProvider


class LLMRouter:
    """LLM 요청을 적절한 백엔드로 라우팅하는 중앙 허브."""

    def __init__(
        self,
        backends: dict[str, LLMBackend],
        providers: dict[str, LLMProvider],
        default_backend: str,
    ) -> None:
        """라우터를 초기화한다.

        Args:
            backends: 백엔드 이름 → LLMBackend 설정 매핑.
            providers: 백엔드 이름 → 초기화된 LLMProvider 인스턴스 매핑.
            default_backend: 요청에 백엔드가 지정되지 않았을 때 사용할 기본 이름.
        """
        self._backends = backends
        self._providers = providers
        self._default = default_backend

    async def send(self, request: LLMRequest) -> LLMResponse:
        """요청을 적절한 백엔드로 라우팅하고 응답을 반환한다."""
        backend_name = request.backend_name or self._default

        if backend_name not in self._providers:
            raise LLMConfigError(
                f"Unknown backend '{backend_name}'. "
                f"Available: {', '.join(self._providers.keys())}"
            )

        provider = self._providers[backend_name]
        logger.info("Routing request to backend '%s'", backend_name)

        response = await provider.send(
            request.system_prompt, request.user_message, request.messages
        )
        return response

    def list_backends(self) -> list[str]:
        """등록된 모든 백엔드의 이름 목록을 반환한다."""
        return list(self._providers.keys())

    def get_default_backend(self) -> str:
        """기본 백엔드 이름을 반환한다."""
        return self._default


def create_router(config_path: str | Path) -> LLMRouter:
    """config.yaml 설정으로부터 LLMRouter를 생성한다.

    Args:
        config_path: config.yaml 파일 경로.

    Returns:
        초기화된 LLMRouter 인스턴스.

    Raises:
        LLMConfigError: 프로바이더가 하나도 설정되지 않았거나 초기화에 실패한 경우.
    """
    _ensure_registry()

    config = load_llm_config(config_path)
    default_name = config.get("default", "")
    providers_config = config.get("providers", {})

    if not providers_config:
        raise LLMConfigError("No LLM providers configured in config.yaml")

    backends: dict[str, LLMBackend] = {}
    providers: dict[str, LLMProvider] = {}

    for name, pconf in providers_config.items():
        backend_type_str = pconf.get("type", "api")
        try:
            backend_type = BackendType(backend_type_str)
        except ValueError:
            raise LLMConfigError(
                f"Invalid backend type '{backend_type_str}' for provider '{name}'"
            )

        backend = LLMBackend(
            name=name,
            backend_type=backend_type,
            model=pconf.get("model", ""),
            api_key_env=pconf.get("api_key_env"),
            command=pconf.get("command"),
            args=pconf.get("args", []),
            timeout=pconf.get("timeout", 120),
        )
        backends[name] = backend

        if backend_type == BackendType.CLI:
            provider_cls = _PROVIDER_REGISTRY.get("cli")
            if provider_cls:
                provider = provider_cls(
                    command=backend.command,
                    args=backend.args,
                    timeout=backend.timeout,
                    name=name,
                )
                providers[name] = provider
        else:
            # API 프로바이더 — 레지스트리에서 이름으로 매칭
            provider_cls = _PROVIDER_REGISTRY.get(name)
            if provider_cls:
                api_key = pconf.get("api_key", "")
                try:
                    provider = provider_cls(
                        model=backend.model,
                        api_key=api_key,
                        name=name,
                    )
                    providers[name] = provider
                except Exception as exc:
                    logger.warning(
                        "Skipping provider '%s': %s", name, exc
                    )
            else:
                logger.warning(
                    "No provider implementation for '%s', skipping.", name
                )

    if default_name and default_name not in providers:
        logger.warning(
            "Default backend '%s' not available. Using first available.",
            default_name,
        )
        # 기본 백엔드가 사용 불가하면 첫 번째 가용 프로바이더로 대체
        default_name = next(iter(providers)) if providers else ""

    if not providers:
        raise LLMConfigError("No valid LLM providers could be initialized")

    return LLMRouter(
        backends=backends,
        providers=providers,
        default_backend=default_name,
    )
