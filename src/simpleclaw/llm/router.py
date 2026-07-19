"""LLM 라우터: 설정 기반 백엔드 선택 및 메시지 라우팅.

config.yaml의 llm 섹션을 읽어 여러 LLM 프로바이더를 초기화하고,
요청마다 지정된(또는 기본) 백엔드로 메시지를 전달한다.

설계 결정:
  - 프로바이더 레지스트리를 지연 임포트로 구성하여 순환 임포트 방지
  - 초기화 실패한 프로바이더는 경고만 남기고 건너뛰어 부분 가용성 보장
"""

from __future__ import annotations

import copy
import logging
from dataclasses import replace
from pathlib import Path

from simpleclaw.config import load_llm_config
from simpleclaw.llm.models import (
    BackendType,
    LLMBackend,
    LLMConfigError,
    LLMRequest,
    LLMResponse,
)
from simpleclaw.llm.providers.base import LLMProvider, TextDeltaCallback
from simpleclaw.llm.profiles import ProviderProfile, get_provider_profile
from simpleclaw.llm.transports import get_transport_class

logger = logging.getLogger(__name__)

# Backward-compatible view for older tests/extensions that inspected the router
# registry directly. create_router() uses simpleclaw.llm.transports instead.
_PROVIDER_REGISTRY: dict[str, type] = {}


def _ensure_registry() -> None:
    """Populate the legacy provider registry view from transport classes."""
    if _PROVIDER_REGISTRY:
        return
    _PROVIDER_REGISTRY["claude"] = get_transport_class("anthropic")
    _PROVIDER_REGISTRY["openai"] = get_transport_class("openai_chat")
    _PROVIDER_REGISTRY["gemini"] = get_transport_class("gemini")
    _PROVIDER_REGISTRY["vertex_gemini"] = get_transport_class("vertex_gemini")
    _PROVIDER_REGISTRY["cli"] = get_transport_class("cli")


def _request_has_attachments(request: LLMRequest) -> bool:
    """현재 요청 messages에 provider-neutral multimodal attachments가 있는지 확인한다.

    BIZ-448 — orchestrator 는 현재 turn 의 user message dict 에 ``attachments`` 를
    싣는다. LLMRequest 자체에는 attachments 필드가 없으므로 messages 를 스캔한다.
    """
    for msg in request.messages or []:
        attachments = msg.get("attachments") if isinstance(msg, dict) else None
        if attachments:
            return True
    return False


def _response_is_empty_final(response: LLMResponse) -> bool:
    """응답이 사용자에게 보일 내용이 전혀 없는 empty final 인지 판정한다.

    BIZ-448 — 보이는 텍스트도 tool call 도 없으면 (예: reasoning budget 소진)
    fallback 재시도 대상이다.
    """
    return not (response.text or "").strip() and not response.tool_calls


class LLMRouter:
    """LLM 요청을 적절한 백엔드로 라우팅하는 중앙 허브."""

    def __init__(
        self,
        backends: dict[str, LLMBackend],
        providers: dict[str, LLMProvider],
        default_backend: str,
        fallback_backend: str | None = None,
        multimodal_backend: str | None = None,
        profiles: dict[str, ProviderProfile] | None = None,
    ) -> None:
        """라우터를 초기화한다.

        Args:
            backends: 백엔드 이름 → LLMBackend 설정 매핑.
            providers: 백엔드 이름 → 초기화된 LLMProvider 인스턴스 매핑.
            default_backend: 요청에 백엔드가 지정되지 않았을 때 사용할 기본 이름.
            fallback_backend: 암묵적 라우팅에서 기본 백엔드가 실패하거나 empty
                final 을 반환할 때 1회 재시도할 백엔드 이름 (BIZ-448).
            multimodal_backend: 첨부가 포함된 암묵적 요청을 라우팅할 백엔드 이름
                (BIZ-448). 미가용 이름은 None 으로 무력화한다.
        """
        self._backends = backends
        self._providers = providers
        self._profiles = profiles or {}
        self._default = default_backend
        # 가용 provider 가 없는 정책 이름은 조용히 비활성화 — 부분 가용성 보장.
        self._fallback = fallback_backend if fallback_backend in providers else None
        self._multimodal = multimodal_backend if multimodal_backend in providers else None

    async def _send_to_backend(
        self,
        backend_name: str,
        request: LLMRequest,
        on_text_delta: TextDeltaCallback | None = None,
    ) -> LLMResponse:
        """선택된 백엔드 provider 로 요청을 실제 전송한다."""
        provider = self._providers[backend_name]

        # BIZ-453 — reasoning hint 는 설정된 요청에만 kwargs 로 전달한다.
        # 미설정 요청의 호출 시그니처를 기존과 동일하게 유지해, reasoning
        # 파라미터가 없는 테스트 대역/구형 provider 대역의 회귀를 막는다.
        extra_kwargs: dict = {}
        if request.reasoning:
            extra_kwargs["reasoning"] = request.reasoning

        if on_text_delta is not None:
            logger.info("Routing streaming request to backend '%s'", backend_name)
            return await provider.stream(
                request.system_prompt,
                request.user_message,
                request.messages,
                request.tools,
                system_blocks=request.system_blocks,
                on_text_delta=on_text_delta,
                max_tokens=request.max_tokens,
                response_mime_type=request.response_mime_type,
                response_schema=request.response_schema,
                require_structured_output=request.require_structured_output,
                **extra_kwargs,
            )

        logger.info("Routing request to backend '%s'", backend_name)
        return await provider.send(
            request.system_prompt,
            request.user_message,
            request.messages,
            request.tools,
            system_blocks=request.system_blocks,
            max_tokens=request.max_tokens,
            response_mime_type=request.response_mime_type,
            response_schema=request.response_schema,
            require_structured_output=request.require_structured_output,
            **extra_kwargs,
        )

    def _fallback_candidate(self, selected_backend: str, explicit_backend: bool) -> str | None:
        """이번 요청에 적용 가능한 fallback 백엔드 이름을 계산한다.

        BIZ-448 정책: explicit 백엔드 지정 요청은 호출자 의도를 존중해 절대
        자동 fallback 하지 않는다. 선택된 백엔드와 fallback 이 같으면(예:
        multimodal 라우팅 결과가 이미 fallback 백엔드) 재시도 의미가 없다.
        """
        if explicit_backend:
            return None
        if not self._fallback:
            return None
        if self._fallback == selected_backend:
            return None
        if self._fallback not in self._providers:
            return None
        return self._fallback

    async def send(
        self,
        request: LLMRequest,
        on_text_delta: TextDeltaCallback | None = None,
    ) -> LLMResponse:
        """요청을 적절한 백엔드로 라우팅하고 응답을 반환한다.

        BIZ-259: ``on_text_delta`` 콜백이 주어지면 프로바이더의 ``stream()`` 경로로
        전환되어 텍스트 델타가 생성될 때마다 콜백이 호출된다. 콜백이 None 이면
        기존 ``send()`` 경로를 그대로 사용 — 호출 측 회귀 0.

        BIZ-448 라우팅 정책 (모두 암묵적 요청, 즉 ``backend_name=None`` 에만 적용):
          1. 첨부가 있으면 multimodal 백엔드로 라우팅.
          2. provider 예외 또는 empty final(텍스트·tool call 모두 없음) 시
             fallback 백엔드로 최대 1회 재시도.
          3. 스트리밍 중에는 fallback 하지 않는다 — 이미 델타가 sink(Telegram 등)
             로 흘러간 뒤 다른 백엔드로 재시도하면 혼합 출력이 생기기 때문.
        """
        explicit_backend = request.backend_name is not None
        backend_name = request.backend_name or self._default
        if not explicit_backend and self._multimodal and _request_has_attachments(request):
            backend_name = self._multimodal

        if backend_name not in self._providers:
            raise LLMConfigError(
                f"Unknown backend '{backend_name}'. "
                f"Available: {', '.join(self._providers.keys())}"
            )

        fallback_name = self._fallback_candidate(backend_name, explicit_backend)

        try:
            response = await self._send_to_backend(backend_name, request, on_text_delta)
        except Exception:
            if fallback_name and on_text_delta is None:
                logger.warning(
                    "Backend '%s' failed; retrying fallback '%s'",
                    backend_name,
                    fallback_name,
                    exc_info=True,
                )
                return await self._send_to_backend(fallback_name, request, None)
            raise

        if fallback_name and on_text_delta is None and _response_is_empty_final(response):
            logger.warning(
                "Backend '%s' returned empty final; retrying fallback '%s'",
                backend_name,
                fallback_name,
            )
            return await self._send_to_backend(fallback_name, request, None)
        return response

    def ensure_model_backend(self, base_backend: str, model: str) -> str | None:
        """기존 백엔드의 credentials/구현을 재사용해 model 만 바꾼 가상 백엔드를 등록한다.

        BIZ-453 — TurnAnalysis 같은 role 전용 호출이 ``llm.default`` 와 독립된
        provider+model 조합을 쓰기 위한 진입점. 같은 조합은 한 번만 등록하고
        이후 호출은 등록된 이름을 재사용한다.

        보안: 이 메서드는 config 로더를 거친 정적 설정(orchestrator 초기화/
        turn 준비 경로)에서만 호출해야 한다. 사용자/runtime tool 입력이 임의
        provider/model 을 만들 수 있는 경로에 노출하지 않는다.

        Args:
            base_backend: ``llm.providers`` 에 정의된 기존 백엔드 이름.
            model: override 할 모델 ID.

        Returns:
            요청에 쓸 백엔드 이름. base 모델과 같으면 base 이름 그대로,
            override 면 ``"{base}#{model}"`` 형식의 가상 백엔드 이름.
            base 미가용/model override 미지원(CLI 등)이면 None — 호출자가
            다음 우선순위(backend/default)로 내려간다.
        """
        base = (base_backend or "").strip()
        target_model = (model or "").strip()
        if not base or not target_model:
            return None
        provider = self._providers.get(base)
        if provider is None:
            logger.warning(
                "Model override base backend '%s' not available; ignoring.", base
            )
            return None
        base_model = getattr(provider, "_model", None)
        if not isinstance(base_model, str):
            # CLI 등 모델 개념이 없는 프로바이더는 override 할 수 없다.
            logger.warning(
                "Backend '%s' does not support model override; ignoring.", base
            )
            return None
        if base_model == target_model:
            return base
        virtual_name = f"{base}#{target_model}"
        if virtual_name in self._providers:
            return virtual_name
        # shallow copy — API client 는 요청 단위 상태가 없으므로 공유해도 안전하고,
        # 프로바이더 재생성(credentials 재주입) 없이 model/이름만 바꾼다.
        clone = copy.copy(provider)
        clone._model = target_model
        clone._name = virtual_name
        self._providers[virtual_name] = clone
        base_config = self._backends.get(base)
        if base_config is not None:
            self._backends[virtual_name] = replace(
                base_config, name=virtual_name, model=target_model
            )
        logger.info(
            "Registered virtual backend '%s' (base=%s model=%s)",
            virtual_name,
            base,
            target_model,
        )
        return virtual_name

    def list_backends(self) -> list[str]:
        """등록된 모든 백엔드의 이름 목록을 반환한다."""
        return list(self._providers.keys())

    def get_default_backend(self) -> str:
        """기본 백엔드 이름을 반환한다."""
        return self._default

    def get_fallback_backend(self) -> str | None:
        """Fallback 백엔드 이름을 반환한다 (미설정/미가용 시 None)."""
        return self._fallback

    def get_multimodal_backend(self) -> str | None:
        """멀티모달 백엔드 이름을 반환한다 (미설정/미가용 시 None)."""
        return self._multimodal

    def get_backend_profile(self, backend_name: str) -> ProviderProfile | None:
        """백엔드에 연결된 provider profile을 반환한다."""
        return self._profiles.get(backend_name)


def create_router(config_path: str | Path) -> LLMRouter:
    """config.yaml 설정으로부터 LLMRouter를 생성한다.

    Args:
        config_path: config.yaml 파일 경로.

    Returns:
        초기화된 LLMRouter 인스턴스.

    Raises:
        LLMConfigError: 프로바이더가 하나도 설정되지 않았거나 초기화에 실패한 경우.
    """
    config = load_llm_config(config_path)
    default_name = config.get("default", "")
    fallback_name = config.get("fallback")
    multimodal_name = config.get("multimodal")
    providers_config = config.get("providers", {})

    if not providers_config:
        raise LLMConfigError("No LLM providers configured in config.yaml")

    backends: dict[str, LLMBackend] = {}
    providers: dict[str, LLMProvider] = {}
    profiles: dict[str, ProviderProfile] = {}

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
            transport=pconf.get("transport"),
            profile=pconf.get("profile"),
            api_key_env=pconf.get("api_key_env"),
            command=pconf.get("command"),
            args=pconf.get("args", []),
            timeout=pconf.get("timeout", 120),
        )
        backends[name] = backend
        profile = get_provider_profile(backend.profile or "generic")
        profiles[name] = profile

        if backend_type == BackendType.CLI:
            try:
                provider_cls = get_transport_class(backend.transport or "cli")
            except LLMConfigError as exc:
                logger.warning(
                    "Skipping provider '%s' (transport=%s profile=%s): %s",
                    name,
                    backend.transport,
                    backend.profile,
                    exc,
                )
                continue
            provider = provider_cls(
                command=backend.command,
                args=backend.args,
                timeout=backend.timeout,
                name=name,
            )
            providers[name] = provider
        else:
            try:
                provider_cls = get_transport_class(backend.transport or "")
            except LLMConfigError as exc:
                logger.warning(
                    "Skipping provider '%s' (transport=%s profile=%s): %s",
                    name,
                    backend.transport,
                    backend.profile,
                    exc,
                )
                continue
            api_key = pconf.get("api_key", "")
            # BIZ-444 — 프로바이더가 EXTRA_CONFIG_KEYS 로 선언한 추가 설정
            # (예: vertex_gemini 의 project/location/credentials_path)만
            # config 블록에서 골라 전달한다. profile hook 의 request_extra_keys 는
            # 후속 route migration 이 provider-specific extras 를 profile 기준으로
            # 옮길 때 쓸 계약이며, 현재 transport 생성 시그니처와 충돌시키지 않는다.
            extra_kwargs = {
                key: pconf[key]
                for key in getattr(provider_cls, "EXTRA_CONFIG_KEYS", ())
                if key in pconf
            }
            try:
                provider = provider_cls(
                    model=backend.model,
                    api_key=api_key,
                    name=name,
                    **extra_kwargs,
                )
                providers[name] = provider
            except Exception as exc:
                logger.warning(
                    "Skipping provider '%s' (transport=%s profile=%s): %s",
                    name,
                    backend.transport,
                    backend.profile,
                    exc,
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

    # BIZ-448 — 미가용 정책 백엔드는 경고 후 비활성화 (부분 가용성 우선).
    if fallback_name and fallback_name not in providers:
        logger.warning(
            "Fallback backend '%s' not available; disabling fallback.", fallback_name
        )
        fallback_name = None
    if multimodal_name and multimodal_name not in providers:
        logger.warning(
            "Multimodal backend '%s' not available; using default routing.",
            multimodal_name,
        )
        multimodal_name = None

    return LLMRouter(
        backends=backends,
        providers=providers,
        default_backend=default_name,
        fallback_backend=fallback_name,
        multimodal_backend=multimodal_name,
        profiles=profiles,
    )
